from celery import Celery
from app.config import settings
from app.processing import process_pdfs_to_excel
from app.database import SessionLocal
from app.models import Task, TaskStatus
from datetime import datetime
import os

celery_app = Celery(__name__, broker=settings.REDIS_URL, backend=settings.REDIS_URL)

@celery_app.task(bind=True)
def process_pdfs_task(self, task_id: int, pdf_paths: list):
    db = SessionLocal()
    try:
        db_task = db.query(Task).filter(Task.id == task_id).first()
        if not db_task:
            return
        db_task.status = TaskStatus.PROCESSING
        db.commit()

        excel_path, log_path = process_pdfs_to_excel(pdf_paths, db_task.user_id, use_ai=True)

        db_task.output_excel = os.path.basename(excel_path)
        db_task.output_log = os.path.basename(log_path)
        db_task.status = TaskStatus.COMPLETED
        db_task.completed_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        db_task.status = TaskStatus.FAILED
        db_task.error_message = str(e)
        db.commit()
        raise e
    finally:
        db.close()