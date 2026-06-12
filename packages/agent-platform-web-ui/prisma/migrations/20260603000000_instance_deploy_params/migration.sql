-- doc 33 §6 (P2-d): native vSphere deploys carry their config in deployParams
-- instead of the legacy agent-prompt Template, so templateId becomes optional.

-- AlterTable
ALTER TABLE "Instance" ALTER COLUMN "templateId" DROP NOT NULL;
ALTER TABLE "Instance" ADD COLUMN "deployParams" JSONB;
