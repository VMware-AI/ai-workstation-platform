/**
 * Database seed — creates a demo account so a freshly-cloned checkout can be
 * logged into without registering first.
 *
 *   email:    demo@local.test
 *   password: demo123456
 *   tenant:   "Demo" (slug "demo"), role OWNER
 *
 * Idempotent: re-running upserts the same rows (and resets the demo password),
 * so it is safe to run repeatedly and on every `prisma migrate reset`.
 *
 * Run: `npx prisma db seed`  (or `npm run seed`)
 * Requires DATABASE_URL (loaded from .env via the seed command).
 */
import { PrismaClient } from "@prisma/client";
import { PrismaPg } from "@prisma/adapter-pg";
import bcrypt from "bcryptjs";

const DEMO_EMAIL = "demo@local.test";
const DEMO_PASSWORD = "demo123456";
const DEMO_TENANT_SLUG = "demo";

function createPrismaClient(): PrismaClient {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error(
      "DATABASE_URL is not set. Copy .env.example to .env and set DATABASE_URL before seeding."
    );
  }
  const adapter = new PrismaPg({ connectionString });
  return new PrismaClient({ adapter });
}

async function main(): Promise<void> {
  // Never seed a production database: this creates a known-credential OWNER
  // account (demo@local.test / demo123456) — a backdoor if it lands in prod.
  // ALLOW_SEED=1 is the explicit escape hatch for the rare non-prod demo that
  // happens to run with NODE_ENV=production.
  if (process.env.NODE_ENV === "production" && process.env.ALLOW_SEED !== "1") {
    throw new Error(
      "Refusing to seed with NODE_ENV=production — this creates a known-credential " +
        "demo OWNER account. Set ALLOW_SEED=1 to override for a non-prod demo."
    );
  }

  const prisma = createPrismaClient();
  try {
    const tenant = await prisma.tenant.upsert({
      where: { slug: DEMO_TENANT_SLUG },
      update: {},
      create: { name: "Demo", slug: DEMO_TENANT_SLUG },
    });

    const passwordHash = await bcrypt.hash(DEMO_PASSWORD, 12);
    const user = await prisma.user.upsert({
      where: { email: DEMO_EMAIL },
      update: { passwordHash },
      create: { name: "Demo User", email: DEMO_EMAIL, passwordHash },
    });

    await prisma.membership.upsert({
      where: { userId_tenantId: { userId: user.id, tenantId: tenant.id } },
      update: { role: "OWNER" },
      create: { userId: user.id, tenantId: tenant.id, role: "OWNER" },
    });

    console.log(
      `Seeded demo account: ${DEMO_EMAIL} / ${DEMO_PASSWORD} ` +
        `(tenant "${tenant.slug}", role OWNER)`
    );
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((err) => {
  console.error("Seed failed:", err);
  process.exit(1);
});
