# Make sure the Celery app is imported when Django starts
from .celery import app as celery_app

# Support PyMySQL as MySQLdb
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

__all__ = ('celery_app',)
