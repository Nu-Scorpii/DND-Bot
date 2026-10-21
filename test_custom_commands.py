import copy
import json
import pytest
from ua_bot.custom import CustomCommands, condition, condition_text, new_field, odds, validate
from ua_bot.dice import values_of
from ua_bot.errors import RollError
from test_avatar import draws


def die(name='Power', expression='1d20', **extra):
    return dict(id=new_field(), name=name, type='dice', dice=expression, **extra)


def table(name='Nation', source='Fire | 25\nWater | 25\nEarth | 25\nNomad | 25', **extra):
    return dict(id=new_field(), name=name, type='table', outcomes=odds(source), **extra)


def command(engine, entries=None, *, kind='roll', name='hero', guild=100, publish=True):
    service = engine.customs
    result = service.create(9, guild, name, kind, 'Campaign ' + kind, is_manager=True)
    if entries is None:
        entries = [die()] if kind == 'roll' else [dict(id=new_field(), name='Rules?', answer='Have fun!')] if kind == 'faq' else [dict(id=new_field(), name='Potion', price=25, description='Heal up.')]
    doc = copy.deepcopy(result['draft'])
    doc['entries'] = entries
    result = service.save(9, guild, result['id'], result['revision'], doc, is_manager=True)
    if publish:
        result = service.visibility(9, guild, result['id'], result['revision'], 'publish', is_manager=True)
    return result


def rig(engine, cmd, mode, value, *, part=None, target=42, uses=1):
    return engine.configure(1, is_dm=True, mode=mode, value=value, target=target,
                            scope=f"custom:{cmd['id']}", part=part, uses=uses)


def run(engine, cmd, request=800, player=42):
    return engine.custom_roll(player, int(cmd['guild']), cmd['id'], request_id=request)


def money(engine, cmd, amount=100, player=42):
    wallet = cmd['published']['wallet_id']
    engine.customs.wallets.change(9, int(cmd['guild']), wallet, player, amount, 'Quest', is_moderator=True)
    return wallet


def buy(engine, cmd, quantity=1, request=500, player=42):
    return engine.customs.purchase(player, int(cmd['guild']), cmd['id'], cmd['published']['entries'][0]['id'],
                                   quantity, cmd['live_revision'], request_id=request)


def test_named_independent_groups_keep_all_six_dice_and_modifier(make_engine):
    engine = make_engine([4, 1, 2, 3, 4, 5, 6, 18])
    cmd = command(engine, [die('Origin', '1d5'), die('Stats', '6d20'), die('Strength', '1d20+2')])
    results = run(engine, cmd)['character']['results']
    assert [r['name'] for r in results] == ['Origin', 'Stats', 'Strength']
    assert [values_of(r['dice']) for r in results] == [[4], [1, 2, 3, 4, 5, 6], [18]]
    assert results[-1]['dice']['total'] == 20


@pytest.mark.parametrize('ticket,expected', [(1, 'Rare'), (2, 'Common'), (4, 'Common')])
def test_weighted_boundaries_are_exact_and_normalized(ticket, expected, make_engine):
    engine = make_engine([ticket])
    entry = table(source='Rare | 12.5%\nCommon | 37.5%')
    assert [r['weight'] for r in entry['outcomes']] == [1, 3]
    cmd = command(engine, [entry])
    assert run(engine, cmd)['character']['results'][0]['outcome'] == expected


@pytest.mark.parametrize('source', ['', 'Name', 'Name | 0', 'Name | -1', 'Name | nan', 'Name | 0.00001',
                                  'Same | 1\nsame | 2', 'A | 1000000001', 'A | 1\nB | 0.0001\nC | 999999999'])
def test_invalid_odds_fail_cleanly(source):
    with pytest.raises(RollError): odds(source)


@pytest.mark.parametrize('name', ['avatar', 'ROLL', '/wallet-transfer', 'rigger', 'custom', 'bad space', '2start', 'a' * 33])
def test_reserved_or_invalid_commands_cannot_shadow_builtins(name, make_engine):
    with pytest.raises(RollError): command(make_engine(), name=name)


def test_conditional_rolls_follow_final_rigged_results_and_skip_descendants(make_engine):
    engine = make_engine([1, 19, 2])
    nation = table(source='Fire | 1\nWater | 1')
    strength = die('Strength', when={'field': nation['id'], 'op': '==', 'value': 'Water'})
    bonus = die('Bonus', '1d3', when={'field': strength['id'], 'op': '>=', 'value': 15})
    cmd = command(engine, [nation, strength, bonus])
    rig(engine, cmd, 'force', 2, part=nation['id'])
    result = run(engine, cmd)['character']['results']
    assert [r['name'] for r in result] == ['Nation', 'Strength', 'Bonus']
    assert result[0]['outcome'] == 'Water'
    engine.rng.values = iter([1])
    assert len(run(engine, cmd, request=801)['character']['results']) == 1
    assert engine.rng.calls == 4


def test_stable_conditions_survive_source_rename_and_reject_invalid_reorder(make_engine):
    engine = make_engine()
    source = die('Bending', '1d100')
    child = die('Strength', when=condition('Bending >= 61', [source], 1))
    cmd = command(engine, [source, child], publish=False)
    doc = copy.deepcopy(cmd['draft'])
    doc['entries'][0]['name'] = 'Magic'
    saved = engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
    assert condition_text(saved['draft']['entries'][1], saved['draft']['entries']) == 'Magic >= 61'
    doc['entries'].reverse()
    with pytest.raises(RollError, match='missing or later'):
        engine.customs.save(9, 100, cmd['id'], saved['revision'], doc, is_manager=True)


@pytest.mark.parametrize('entries', [[die('Bad=Name')], [die('Stats', '100d20'), die('More', '100d20'), die()],
                                     [die(str(i)) for i in range(21)],
                                     [dict(id=new_field(), name='Bad odds', type='table', outcomes=[dict(weight=1)])]])
def test_invalid_templates_are_rejected_without_partial_saves(entries, make_engine):
    engine = make_engine()
    cmd = command(engine, publish=False)
    doc = copy.deepcopy(cmd['draft']); doc['entries'] = entries
    with pytest.raises(RollError):
        engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
    assert engine.customs.get(100, cmd['id']) == cmd


def test_drafts_publish_explicitly_with_conflict_detection_archive_and_restore(make_engine):
    engine = make_engine([3, 4])
    cmd = command(engine)
    doc = copy.deepcopy(cmd['draft']); doc['title'] = 'Edited title'
    changed = engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
    assert run(engine, cmd)['character']['title'] != 'Edited title'
    with pytest.raises(RollError, match='changed elsewhere'):
        engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_manager=True)
    with pytest.raises(RollError, match='Unpublish'):
        engine.customs.save(9, 100, cmd['id'], changed['revision'], doc, name='renamed', is_manager=True)
    current = engine.customs.visibility(9, 100, cmd['id'], changed['revision'], 'publish', is_manager=True)
    assert run(engine, cmd, request=801)['character']['title'] == 'Edited title'
    for action in ['archive', 'restore']:
        current = engine.customs.visibility(9, 100, cmd['id'], current['revision'], action, is_manager=True)
        assert current['published'] is None
        with pytest.raises(RollError, match='not published'): run(engine, cmd, request=802)
    assert not current['archived'] and current['draft']['title'] == 'Edited title'


@pytest.mark.parametrize('kind', ['roll', 'faq', 'shop'])
def test_bot_creator_does_not_bypass_server_builder_permissions(kind, make_engine):
    engine = make_engine()
    with pytest.raises(RollError, match='permissions'):
        engine.customs.create(1, 100, 'test', kind, 'Test')
    if kind == 'roll':
        with pytest.raises(RollError): engine.customs.create(9, 100, 'test', kind, 'Test', is_moderator=True)
    else:
        assert engine.customs.create(9, 100, 'test', kind, 'Test', is_moderator=True)['kind'] == kind


def test_server_and_bot_owner_scoping_is_enforced(make_engine):
    engine = make_engine([5, 6])
    first = command(engine)
    other = command(engine, guild=200)
    assert first['id'] != other['id'] and first['name'] == other['name']
    with pytest.raises(RollError): engine.custom_roll(42, 200, first['id'])
    with pytest.raises(RollError): CustomCommands(engine.store, 2).get(100, first['id'])
    rig(engine, first, 'force', 20, part=first['draft']['entries'][0]['id'])
    assert run(engine, other)['character']['results'][0]['dice']['total'] == 5
    assert run(engine, first, request=801)['character']['results'][0]['dice']['total'] == 20


@pytest.mark.parametrize('mode,value,draws_,expected', [('good', 100, [4], 20), ('bad', 100, [18], 1),
    ('shift', 3, [4], 7), ('force', 19, [4], 19), ('extra_high', 3, [4, 16, 7, 12], 16),
    ('extra_low', 3, [10, 8, 4, 9], 4)])
def test_every_numeric_rigger_mode_applies_to_custom_step(mode, value, draws_, expected, make_engine):
    engine = make_engine(draws_)
    cmd = command(engine)
    rig(engine, cmd, mode, value, part=cmd['draft']['entries'][0]['id'])
    result = run(engine, cmd)
    assert result['character']['results'][0]['dice']['total'] == expected
    assert not engine.store.controls(1)['rules']
    assert 'extra_' not in json.dumps(result['character']) and 'remaining' not in json.dumps(result['character'])


def test_stacked_custom_rigs_consume_once_per_character_and_preserve_avatar_and_roll(make_engine):
    engine = make_engine([4, 1, 2, 3, 4, 5, 6, 2, 3] + draws(1))
    cmd = command(engine, [die('Origin', '1d5'), die('Stats', '6d20'), die('Power')])
    rig(engine, cmd, 'good', 100, uses=2)
    rig(engine, cmd, 'force', 7, part=cmd['draft']['entries'][-1]['id'], uses=None)
    result = run(engine, cmd)
    assert [r['dice']['total'] for r in result['character']['results']] == [5, 120, 7]
    assert [r['remaining'] for r in engine.store.controls(1)['rules']] == [1, None]
    assert engine.roll(42, 100, '1d20')['dice']['total'] == 3
    engine.avatar(42, 100)
    assert [r['remaining'] for r in engine.store.controls(1)['rules']] == [1, None]


def test_text_face_rig_and_conditional_exact_outcome(make_engine):
    engine = make_engine(['Fire', 10])
    entry = die('Element', '1d["Fire","Water","Earth"]')
    child = die('Power', when={'field': entry['id'], 'op': '==', 'value': 'Water'})
    cmd = command(engine, [entry, child])
    rig(engine, cmd, 'face', 'Water', part=entry['id'])
    assert len(run(engine, cmd)['character']['results']) == 2
    with pytest.raises(RollError): rig(engine, cmd, 'force', 2, part=entry['id'])


def test_conditional_rigs_use_character_duration_and_survive_restart(tmp_path, make_engine):
    path = tmp_path / 'custom.sqlite3'
    engine = make_engine([1], path)
    source = die('Bending')
    child = die('Power', when={'field': source['id'], 'op': '>', 'value': 10})
    cmd = command(engine, [source, child])
    rig(engine, cmd, 'force', 19, part=child['id'], uses=2)
    rig(engine, cmd, 'shift', 1, part=child['id'], uses=None)
    first = run(engine, cmd)
    assert first['notice'] and [r['remaining'] for r in engine.store.controls(1)['rules']] == [1, None]
    engine.store.close()
    restarted = make_engine([], path)
    assert run(restarted, cmd)['replayed']
    assert restarted.rng.calls == 0
    assert [r['remaining'] for r in restarted.store.controls(1)['rules']] == [1, None]


def test_receipt_failure_rolls_back_rig_duration_and_result(make_engine, monkeypatch):
    engine = make_engine([2])
    cmd = command(engine)
    rig(engine, cmd, 'good', 100)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(engine.store, 'remember', fail)
    with pytest.raises(OSError): run(engine, cmd)
    assert engine.store.controls(1)['rules'][0]['remaining'] == 1


def test_shop_purchase_is_atomic_and_duplicate_quote_never_charges_twice(make_engine):
    engine = make_engine()
    cmd = command(engine, kind='shop')
    wallet = money(engine, cmd)
    first = buy(engine, cmd, quantity=2)
    second = buy(engine, cmd, quantity=2)
    assert first['balance'] == second['balance'] == 50 and second['replayed']
    assert engine.customs.inventory(42, 100, cmd['id']) == [{'name': 'Potion', 'quantity': 2}]
    snapshot = engine.customs.wallets.snapshot(100, wallet, 42)
    assert snapshot['count'] == 2 and snapshot['entries'][0]['delta'] == -50
    assert engine.rng.calls == 0


@pytest.mark.parametrize('quantity', [0, -1, 101, True, 1.5, '2'])
def test_invalid_purchase_quantities_never_change_money(quantity, make_engine):
    engine = make_engine(); cmd = command(engine, kind='shop'); wallet = money(engine, cmd)
    with pytest.raises(RollError): buy(engine, cmd, quantity=quantity)
    assert engine.customs.wallets.snapshot(100, wallet, 42)['balance'] == 100
    assert engine.customs.inventory(42, 100, cmd['id']) == []


def test_stale_shop_quote_foreign_wallet_and_insufficient_balance_cannot_charge(make_engine):
    engine = make_engine(); cmd = command(engine, kind='shop'); wallet = money(engine, cmd)
    with pytest.raises(RollError): buy(engine, cmd, quantity=5)
    doc = copy.deepcopy(cmd['draft']); doc['entries'][0]['price'] = 30
    edited = engine.customs.save(9, 100, cmd['id'], cmd['revision'], doc, is_moderator=True)
    current = engine.customs.visibility(9, 100, cmd['id'], edited['revision'], 'publish', is_moderator=True)
    with pytest.raises(RollError, match='changed'): buy(engine, cmd)
    doc['wallet_id'] = engine.customs.wallets.ensure_default(200)['id']
    with pytest.raises(RollError): engine.customs.save(9, 100, cmd['id'], current['revision'], doc, is_moderator=True)
    assert engine.customs.wallets.snapshot(100, wallet, 42)['balance'] == 100


def test_shop_receipt_failure_rolls_back_balance_ledger_and_inventory(make_engine, monkeypatch):
    engine = make_engine(); cmd = command(engine, kind='shop'); wallet = money(engine, cmd)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(engine.customs.wallets, '_remember', fail)
    with pytest.raises(OSError): buy(engine, cmd)
    snapshot = engine.customs.wallets.snapshot(100, wallet, 42)
    assert snapshot['balance'] == 100 and snapshot['count'] == 1
    assert engine.customs.inventory(42, 100, cmd['id']) == []


def test_shop_receipt_survives_restart_and_new_price(tmp_path, make_engine):
    path = tmp_path / 'shop.sqlite3'
    engine = make_engine([], path); cmd = command(engine, kind='shop'); money(engine, cmd)
    first = buy(engine, cmd)
    engine.customs.visibility(9, 100, cmd['id'], cmd['revision'], 'unpublish', is_moderator=True)
    engine.store.close()
    restarted = make_engine([], path)
    assert buy(restarted, cmd) == {**first, 'replayed': True}


def test_two_connections_cannot_overspend(tmp_path, make_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from ua_bot.engine import DiceEngine
    from ua_bot.storage import Store
    path = tmp_path / 'race.sqlite3'
    engine = make_engine([], path); cmd = command(engine, kind='shop'); wallet = money(engine, cmd, 25)
    barrier = Barrier(2)
    def checkout(request):
        store = Store(path)
        try:
            other = DiceEngine(store, 1)
            barrier.wait(timeout=5)
            try: return buy(other, cmd, request=request)['balance']
            except RollError: return 'insufficient'
        finally: store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(checkout, [601, 602]))
    assert sorted(results, key=str) == [0, 'insufficient']
    assert engine.customs.wallets.snapshot(100, wallet, 42)['balance'] == 0
    assert engine.customs.inventory(42, 100, cmd['id'])[0]['quantity'] == 1
