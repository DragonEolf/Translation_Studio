"""Run or resume an existing document without launching a second queue worker."""
import argparse
import asyncio
import shutil
import sqlite3
from collections import Counter
from sqlalchemy import select
from app.config import Settings
from app.main import create_app
from app.models import Document,Job,Issue,Block,LayoutResult,Page


async def run(document_id, language, render_only=False):
    settings = Settings()
    root = settings.studio_data_dir.resolve()
    if not (root/"before-repair.sqlite3").exists():
        with sqlite3.connect(root/"studio.sqlite3") as source, sqlite3.connect(root/"before-repair.sqlite3") as backup:
            source.backup(backup)
    previous = root/document_id/"final.pdf"
    if previous.exists() and not previous.with_name("before-repair.pdf").exists():
        shutil.copy2(previous,previous.with_name("before-repair.pdf"))
    app = create_app(settings)
    with app.state.sessions() as session:
        document = session.get(Document,document_id)
        if document is None:
            raise ValueError("Unknown document")
        if session.scalar(select(Job.id).where(Job.document_id==document_id,Job.status.in_(["queued","running"]))):
            raise ValueError("A job is already active for this document")
        if render_only:
            for page in session.scalars(select(Page).where(Page.document_id==document_id)):
                page.dirty=True
        job = Job(document_id=document_id,operation="render" if render_only else "translate",status="running",payload={"target_language":language,"script":"native"})
        session.add(job)
        session.commit()
        identity = job.id
    print("Job:",identity,flush=True)
    try:
        status = await app.state.orchestrator.run(identity)
    except Exception as exc:
        with app.state.sessions() as session:
            job = session.get(Job,identity)
            job.status="failed"
            job.error_code=getattr(exc,"code","processing_failed")
            job.error_message=getattr(exc,"message","Processing failed")
            session.get(Document,document_id).status="failed"
            session.commit()
        raise
    with app.state.sessions() as session:
        job = session.get(Job,identity)
        job.status,job.progress=status,1
        session.get(Document,document_id).status=status
        session.commit()
        issues = list(session.scalars(select(Issue).where(Issue.document_id==document_id,Issue.resolution!="resolved")))
        print("Status:",status)
        print("Issues:",dict(Counter(i.code for i in issues)))
        print("Layouts:",dict(Counter(l.fit_status for l in session.scalars(select(LayoutResult).join(Block,Block.id==LayoutResult.block_id).where(Block.document_id==document_id)))))
        print("Output:",previous if previous.exists() else previous.with_name("draft.pdf"))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--target",default="hi")
    parser.add_argument("--render-only",action="store_true")
    args=parser.parse_args()
    asyncio.run(run(args.document_id,args.target,args.render_only))
