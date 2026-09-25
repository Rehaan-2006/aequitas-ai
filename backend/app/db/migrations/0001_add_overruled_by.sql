-- Migration: Add overruled_by foreign key to cases table
-- Purpose: Allow the Validity/Citator Agent to substitute replacement cases for overruled ones
-- Note: Falls back to dropping the overruled case when overruled_by is NULL

ALTER TABLE cases ADD COLUMN overruled_by UUID REFERENCES cases(id);
