"""Startup reconciliation runs once per process, not once per gateway reconnect."""

import pytest

import app.bot.main as bot_main


@pytest.fixture(autouse=True)
def _reset_startup_flags():
    bot_main._startup_reconciliation_done = False
    bot_main._startup_reconciliation_running = False
    yield
    bot_main._startup_reconciliation_done = False
    bot_main._startup_reconciliation_running = False


async def test_second_call_does_not_scan(monkeypatch):
    bot_main._startup_reconciliation_done = False
    calls = {"n": 0}

    async def scanner(_db, _guilds):
        calls["n"] += 1

    monkeypatch.setattr("app.bot.self_healing.run_self_healing", scanner)

    await bot_main.run_startup_reconciliation(None, [])
    await bot_main.run_startup_reconciliation(None, [])

    assert calls["n"] == 1
    assert bot_main._startup_reconciliation_done is True


async def test_raised_scanner_leaves_the_flag_false(monkeypatch):
    bot_main._startup_reconciliation_done = False

    async def scanner(_db, _guilds):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.bot.self_healing.run_self_healing", scanner)

    with pytest.raises(RuntimeError, match="boom"):
        await bot_main.run_startup_reconciliation(None, [])
    assert bot_main._startup_reconciliation_done is False

    calls = {"n": 0}

    async def ok(_db, _guilds):
        calls["n"] += 1

    monkeypatch.setattr("app.bot.self_healing.run_self_healing", ok)
    await bot_main.run_startup_reconciliation(None, [])
    assert calls["n"] == 1
    assert bot_main._startup_reconciliation_done is True


async def test_overlapping_call_does_not_scan(monkeypatch):
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def scanner(_db, _guilds):
        calls["n"] += 1
        started.set()
        await release.wait()

    monkeypatch.setattr("app.bot.self_healing.run_self_healing", scanner)

    first = asyncio.create_task(bot_main.run_startup_reconciliation(None, []))
    await started.wait()
    await bot_main.run_startup_reconciliation(None, [])
    release.set()
    await first

    assert calls["n"] == 1
    assert bot_main._startup_reconciliation_done is True
    assert bot_main._startup_reconciliation_running is False
