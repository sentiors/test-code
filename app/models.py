from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    username = Column(String, primary_key=True)  # Username sebagai primary key
    password = Column(String, nullable=False)    # Password untuk login
    class_name = Column(String, nullable=False)  # Nama kelas (enum: 10_sija1, 10_sija2, dll.)

class Lab(Base):
    __tablename__ = 'labs'
    lab_id = Column(String, primary_key=True)    # ID lab sebagai primary key
    scheme_path = Column(String, nullable=False) # Path ke file skema lab

class GradingResult(Base):
    __tablename__ = 'grading_results'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, ForeignKey('users.username'), nullable=False)
    class_name = Column(String, nullable=False)  # Tambahkan ini
    lab_id = Column(String, ForeignKey('labs.lab_id'), nullable=False)
    score = Column(Float, nullable=False)
    feedback = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="ongoing")  # Tambahkan kolom status
    duration = Column(Float)
