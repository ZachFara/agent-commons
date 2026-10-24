-- Hosted mode (decision 0018): admins may use the hosted dashboard to file human reviews.
ALTER TABLE reporters ADD COLUMN is_admin boolean NOT NULL DEFAULT false;
