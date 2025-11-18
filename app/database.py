from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import scoped_session, sessionmaker

# Konfigurasi database
DATABASE_URI = 'sqlite:////opt/grading/db.sqlite'
engine = create_engine(DATABASE_URI)
metadata = MetaData()

# Session factory
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def init_db():
    import models  # Pastikan ada file models.py
    models.Base.metadata.create_all(bind=engine)
    print("Database initialized successfully")  # Debugging
