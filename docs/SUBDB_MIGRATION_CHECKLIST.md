# Sub DB Migration Checklist (pgAdmin)

Use this checklist to verify hospital-scoped data is stored in tenant DB (sub DB), not platform DB.

## Inputs you need

- `hospital_id` (UUID)
- `tenant_database_name` for that hospital
- A test user email/staff id created from Hospital Admin APIs

## Step 1: Verify hospital -> tenant mapping (Platform DB)

Run on **platform DB**:

```sql
SELECT id, name, tenant_database_name
FROM hospitals
WHERE id = '<hospital_id>';
```

Expected:

- one row returned
- `tenant_database_name` is not null

## Step 2: Verify tenant DB exists

Run on postgres maintenance DB (or platform DB connection with access):

```sql
SELECT datname
FROM pg_database
WHERE datname = '<tenant_database_name>';
```

Expected:

- one row returned

## Step 3: Verify core tables exist in tenant DB

Connect to **tenant DB** (`tenant_database_name`) and run:

```sql
SELECT
  to_regclass('public.hospitals')  AS hospitals_tbl,
  to_regclass('public.users')      AS users_tbl,
  to_regclass('public.roles')      AS roles_tbl,
  to_regclass('public.user_roles') AS user_roles_tbl,
  to_regclass('public.departments') AS departments_tbl,
  to_regclass('public.wards')       AS wards_tbl,
  to_regclass('public.beds')        AS beds_tbl;
```

Expected:

- all values are non-null table names

## Step 4: Create test data via APIs

Run these in app:

1. create department
2. create staff (doctor/nurse/receptionist/lab/pharmacist)
3. create ward and bed
4. create one lab POST entity
5. create one pharmacy POST entity

## Step 5: Verify hospital business data in tenant DB

Run on **tenant DB**:

```sql
SELECT count(*) FROM departments WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM staff_profiles WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM doctor_profiles WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM nurse_profiles WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM receptionist_profiles WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM wards WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM beds WHERE hospital_id = '<hospital_id>';
```

Expected:

- counts increase after API writes

## Step 6: Spot-check lab/pharmacy rows in tenant DB

Run on **tenant DB** (adjust if table names differ in your schema):

```sql
-- lab examples
SELECT to_regclass('public.lab_equipment');
SELECT count(*) FROM lab_equipment WHERE hospital_id = '<hospital_id>';

-- pharmacy examples
SELECT to_regclass('public.pharmacy_medicines');
SELECT count(*) FROM pharmacy_medicines WHERE hospital_id = '<hospital_id>';
```

If a table name is unknown:

```sql
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND (tablename ILIKE '%lab%' OR tablename ILIKE '%pharmacy%')
ORDER BY tablename;
```

## Step 7: Confirm platform DB is not receiving business module rows

Run on **platform DB**:

```sql
SELECT count(*) FROM departments WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM wards WHERE hospital_id = '<hospital_id>';
SELECT count(*) FROM beds WHERE hospital_id = '<hospital_id>';
```

Expected:

- should be `0` for tenant-scoped business data

## Step 8: Auth mirror sanity (platform DB)

If login resolution uses platform users, verify mirror records:

```sql
SELECT id, email, staff_id, hospital_id
FROM users
WHERE hospital_id = '<hospital_id>'
ORDER BY created_at DESC
LIMIT 20;
```

Expected:

- newly created staff visible for login resolution

## Common failure patterns

- `relation "users" does not exist` in tenant DB
  - tenant schema incomplete; ensure schema bootstrap/create_all ran
- staff created in tenant but login fails
  - platform auth mirror rows missing or inconsistent
- lab/pharmacy writes appear in platform
  - route/session misbinding (`get_platform_db_session` used by mistake)

## Quick SQL to compare tenant vs platform counts

Run same query on both DBs and compare:

```sql
SELECT '<db_name>' AS db, count(*) AS departments
FROM departments
WHERE hospital_id = '<hospital_id>';
```

Tenant should show business rows; platform should not.

