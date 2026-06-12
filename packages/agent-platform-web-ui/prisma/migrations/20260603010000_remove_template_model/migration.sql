-- doc 33 §6 (P2-c.3): provisioning is fully deployParams-driven; the legacy
-- agent-prompt Template is removed. Drop the Instance FK column first (clears
-- the constraint), then the table.

ALTER TABLE "Instance" DROP COLUMN "templateId";
DROP TABLE "Template";
