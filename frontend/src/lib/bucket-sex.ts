/** Sex each bucket is reserved for, mirroring backend/app/schemas/animals.py
 * (and the ck_animals_bucket_sex CHECK). Buckets absent here take both.
 * Shared by the herd list's create dialog and the profile's move dialog —
 * both enums (AnimalCreateInCurrentBucket, MoveInToBucket) carry these same
 * wire values. Pinned to the backend CHECK by bucket-sex.test.ts. */
export const BUCKET_REQUIRED_SEX: Record<string, string> = {
  MALE_KIDS: "M",
  FEMALE_KIDS: "F",
  PREGNANCY_EARLY: "F",
  PREGNANCY_LATE: "F",
  DELIVERY: "F",
  RESTING: "F",
};

export const bucketAllowsSex = (bucket: string, sex: string) =>
  (BUCKET_REQUIRED_SEX[bucket] ?? sex) === sex;
