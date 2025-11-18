from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
from datetime import datetime

# Konfigurasi database
DATABASE_URI = 'sqlite:////opt/grading/db.sqlite'
engine = create_engine(DATABASE_URI)

# Buat metadata dan muat tabel lama
metadata = MetaData()
metadata.reflect(bind=engine)  # Memuat metadata dari database
Session = sessionmaker(bind=engine)
session = Session()

# Definisikan tabel lama
if 'grading_results' not in metadata.tables:
    print("Tabel grading_results tidak ditemukan. Pastikan database sudah benar.")
    exit(1)

old_table = Table('grading_results', metadata, autoload_with=engine)

# Buat tabel baru dengan kolom tambahan 'status'
new_table = Table(
    'grading_results_new',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('username', String, nullable=False),
    Column('class_name', String, nullable=False),  # Kolom baru
    Column('lab_id', String, nullable=False),
    Column('score', Float, nullable=False),
    Column('feedback', String, nullable=True),
    Column('timestamp', DateTime, default=datetime.utcnow),
    Column('status', String, default="ongoing"),  # Kolom baru: status
    extend_existing=True  # Memastikan tidak terjadi error jika tabel sudah ada
)

# Buat tabel baru di database jika belum ada
metadata.create_all(engine)

# Pindahkan data dari tabel lama ke tabel baru
try:
    with engine.begin() as conn:
        result = conn.execute(old_table.select())
        for row in result:
            conn.execute(
                new_table.insert().values(
                    id=row.id,
                    username=row.username,
                    class_name=row.class_name if hasattr(row, 'class_name') else '',  # Isi dengan nilai default atau sesuaikan
                    lab_id=row.lab_id,
                    score=row.score,
                    feedback=row.feedback,
                    timestamp=row.timestamp,
                    status="ongoing"  # Set default status
                )
            )
except Exception as e:
    print(f"Error saat memindahkan data: {e}")
    exit(1)

# Hapus tabel lama jika masih ada
if 'grading_results' in metadata.tables:
    try:
        old_table.drop(engine)
    except Exception as e:
        print(f"Error saat menghapus tabel lama: {e}")
        exit(1)

# Ganti nama tabel baru menjadi nama lama
try:
    with engine.connect() as conn:
        conn.execute(text('ALTER TABLE grading_results_new RENAME TO grading_results'))
        conn.commit()
except Exception as e:
    print(f"Error saat mengganti nama tabel: {e}")
    exit(1)

# Commit perubahan
session.commit()
session.close()
print("Migration completed successfully.")
