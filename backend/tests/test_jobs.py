import asyncio

from shogai.jobs import JobManager


async def test_drain_waits_for_running_jobs():
    jobs = JobManager()
    job = jobs.create("rebuild", "ana", "Ana", "Rebuild Ana's profile")
    job.task = asyncio.create_task(asyncio.sleep(0.1, result="written"))
    assert await jobs.drain(timeout=5) == 0
    assert job.task.result() == "written"


async def test_drain_gives_up_after_timeout():
    jobs = JobManager()
    job = jobs.create("rebuild", "ana", "Ana", "Rebuild Ana's profile")
    job.task = asyncio.create_task(asyncio.sleep(10))
    assert await jobs.drain(timeout=0.05) == 1
    job.task.cancel()


async def test_drain_without_running_jobs_returns_at_once():
    assert await JobManager().drain(timeout=5) == 0
