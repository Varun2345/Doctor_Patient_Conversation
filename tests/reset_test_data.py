"""Remove the data created by the automated tests - and ONLY that data.

Deletes consultations, patients and stored audio that belong to the synthetic test
accounts (test.doctor@pilot.local and the temporary test.doctor.b+* / signup.test+*
accounts), then those temporary accounts. Records of real users of the test database
are left untouched. Refuses to run when the database is marked hospital-internal.
"""
import gridfs

from app.config import settings
from app.db import get_db

assert not settings.db_internal, "Refusing: this database is configured as hospital-internal."
db = get_db()
test_doctors = list(db.doctors.find({"$or": [
    {"email": "test.doctor@pilot.local"},
    {"email": {"$regex": r"^(test\.doctor\.b|signup\.test)\+.*@pilot\.local$"}},
]}, {"doctorId": 1, "email": 1}))
ids = [d["doctorId"] for d in test_doctors]
cids = [c["consultationId"] for c in db.consultations.find({"doctorId": {"$in": ids}}, {"consultationId": 1})]
bucket = gridfs.GridFSBucket(db, bucket_name="audio")
removed_files = 0
for f in db["audio.files"].find({"metadata.consultationId": {"$in": cids}}, {"_id": 1}):
    bucket.delete(f["_id"])
    removed_files += 1
print("consultations:", db.consultations.delete_many({"consultationId": {"$in": cids}}).deleted_count)
print("patients:", db.patients.delete_many({"ownerDoctorId": {"$in": ids}}).deleted_count)
print("audio files:", removed_files)
print("temporary test doctors:", db.doctors.delete_many({
    "email": {"$regex": r"^(test\.doctor\.b|signup\.test)\+.*@pilot\.local$"}}).deleted_count)
for cid in cids:
    import shutil
    shutil.rmtree(settings.recordings_dir / cid, ignore_errors=True)
