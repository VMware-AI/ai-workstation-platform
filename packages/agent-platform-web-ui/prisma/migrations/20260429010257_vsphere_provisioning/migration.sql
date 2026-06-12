-- CreateEnum
CREATE TYPE "PoolType" AS ENUM ('docker', 'vsphere');

-- AlterEnum
-- This migration adds more than one value to an enum.
-- With PostgreSQL versions 11 and earlier, this is not possible
-- in a single migration. This can be worked around by creating
-- multiple migrations, each migration adding only one value to
-- the enum.


ALTER TYPE "InstanceStatus" ADD VALUE 'PROVISIONING';
ALTER TYPE "InstanceStatus" ADD VALUE 'INITIALIZING';

-- AlterEnum
-- This migration adds more than one value to an enum.
-- With PostgreSQL versions 11 and earlier, this is not possible
-- in a single migration. This can be worked around by creating
-- multiple migrations, each migration adding only one value to
-- the enum.


ALTER TYPE "ProviderType" ADD VALUE 'deepseek';
ALTER TYPE "ProviderType" ADD VALUE 'qwen';
ALTER TYPE "ProviderType" ADD VALUE 'ollama';

-- AlterTable
ALTER TABLE "Instance" ADD COLUMN     "agentTokenExpiresAt" TIMESTAMP(3),
ADD COLUMN     "agentTokenHash" TEXT,
ADD COLUMN     "computePoolId" TEXT,
ADD COLUMN     "errorMessage" TEXT,
ADD COLUMN     "ipAddress" TEXT,
ADD COLUMN     "provisionerJobId" TEXT,
ADD COLUMN     "vmRefId" TEXT;

-- CreateTable
CREATE TABLE "ComputePool" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "type" "PoolType" NOT NULL,
    "config" JSONB NOT NULL DEFAULT '{}',
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ComputePool_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ProvisionEvent" (
    "id" TEXT NOT NULL,
    "instanceId" TEXT NOT NULL,
    "event" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "metadata" JSONB NOT NULL DEFAULT '{}',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProvisionEvent_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "ComputePool_tenantId_idx" ON "ComputePool"("tenantId");

-- CreateIndex
CREATE INDEX "ProvisionEvent_instanceId_createdAt_idx" ON "ProvisionEvent"("instanceId", "createdAt");

-- AddForeignKey
ALTER TABLE "Instance" ADD CONSTRAINT "Instance_computePoolId_fkey" FOREIGN KEY ("computePoolId") REFERENCES "ComputePool"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ComputePool" ADD CONSTRAINT "ComputePool_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProvisionEvent" ADD CONSTRAINT "ProvisionEvent_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES "Instance"("id") ON DELETE CASCADE ON UPDATE CASCADE;
