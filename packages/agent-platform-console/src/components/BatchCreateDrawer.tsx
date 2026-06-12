import { useMemo, useState } from "react";
import { Drawer } from "@/components/ui/drawer";
import { Button } from "@/components/ui/button";
import { api, type DeploymentCreate } from "@/lib/api";

/**
 * W-1: batch create drawer for the VMs tab.
 *
 * M1 keeps all fields as plain text inputs because /admin/tenants is still a
 * stub. The "Users" textarea takes one user per line; we synthesize an
 * intended_name as ``<owner>-<n>`` so the API's uniqueness check (see
 * api/deployments.py create_deployment) passes for repeat users with a manual
 * suffix. Template + image_version come from the form so the operator can
 * pick whichever signed OVA was just promoted.
 *
 * doc 30 PR-Buf-1 polish (2026-06-01):
 *   - P-2 client-side guards: tenant kebab-case, duplicate-user detection,
 *     row count preview before submit
 *   - P-3 keep last submitted snapshot + Retry button on the error banner so
 *     the operator does not retype after a transient backend rejection
 */
type Props = {
  open: boolean;
  onClose: () => void;
  /** Called with the new deployment id on a successful POST. */
  onCreated?: (deploymentId: string) => void;
};

type FieldErrors = Partial<
  Record<"tenant" | "template" | "image_version" | "users", string>
>;

const PLACEHOLDER_TEMPLATE =
  "[templates] agent-platform-ubuntu22/agent-platform-ubuntu22.vmtx";

// Kebab-case: starts with letter/digit, then letters/digits/hyphens only.
// Mirrors the server's tenant_id regex in api/deployments.py.
const TENANT_FORMAT = /^[a-z0-9][a-z0-9-]*$/;

function parseUsers(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function findDuplicates(users: string[]): string[] {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const u of users) {
    if (seen.has(u)) dupes.add(u);
    seen.add(u);
  }
  return [...dupes];
}

function validate(fields: {
  tenant: string;
  template: string;
  image_version: string;
  users: string[];
}): FieldErrors {
  const errs: FieldErrors = {};
  if (!fields.tenant) {
    errs.tenant = "Tenant id is required";
  } else if (!TENANT_FORMAT.test(fields.tenant)) {
    errs.tenant = "Tenant id must be kebab-case (a-z, 0-9, hyphens)";
  }
  if (!fields.template) errs.template = "Template path is required";
  if (!fields.image_version) errs.image_version = "Image version is required";
  if (fields.users.length === 0) {
    errs.users = "At least one user required (one per line)";
  } else {
    const dupes = findDuplicates(fields.users);
    if (dupes.length > 0) {
      errs.users = `Duplicate user${dupes.length > 1 ? "s" : ""}: ${dupes.join(", ")}`;
    }
  }
  return errs;
}

export function BatchCreateDrawer({ open, onClose, onCreated }: Props) {
  const [tenant, setTenant] = useState("");
  const [template, setTemplate] = useState(PLACEHOLDER_TEMPLATE);
  const [imageVersion, setImageVersion] = useState("");
  const [usersRaw, setUsersRaw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const users = useMemo(() => parseUsers(usersRaw), [usersRaw]);

  const submitBody = (): DeploymentCreate => ({
    tenant_id: tenant,
    template,
    image_version: imageVersion,
    items: users.map((owner, i) => ({
      owner_id: owner,
      intended_name: `${owner}-${String(i + 1).padStart(3, "0")}`,
    })),
  });

  const runSubmit = async () => {
    const fieldErrors = validate({
      tenant,
      template,
      image_version: imageVersion,
      users,
    });
    setErrors(fieldErrors);
    if (Object.keys(fieldErrors).length > 0) return;

    setSubmitError(null);
    setSubmitting(true);
    try {
      const created = await api.createDeployment(submitBody());
      onCreated?.(created.id);
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runSubmit();
  };

  return (
    <Drawer open={open} onClose={onClose} title="Batch Create VMs">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Tenant id" error={errors.tenant}>
          <input
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={tenant}
            onChange={(e) => setTenant(e.target.value)}
            placeholder="acme-corp"
          />
        </Field>
        <Field label="Template path" error={errors.template}>
          <input
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          />
        </Field>
        <Field label="Image version" error={errors.image_version}>
          <input
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={imageVersion}
            onChange={(e) => setImageVersion(e.target.value)}
            placeholder="v0.1.0"
          />
        </Field>
        <Field label="Users (one per line)" error={errors.users}>
          <textarea
            className="h-32 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
            value={usersRaw}
            onChange={(e) => setUsersRaw(e.target.value)}
            placeholder="alice&#10;bob&#10;carol"
          />
        </Field>
        {submitError && (
          <div
            className="flex items-center justify-between gap-3 rounded-md border border-destructive bg-destructive/10 px-3 py-2 text-xs text-destructive"
            data-testid="submit-error"
          >
            <span>{submitError}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void runSubmit()}
              disabled={submitting}
            >
              Retry
            </Button>
          </div>
        )}
        <div className="flex items-center justify-between pt-2">
          <span
            className="text-xs text-muted-foreground"
            data-testid="row-count-preview"
          >
            {users.length === 0
              ? "No VMs queued"
              : `Will create ${users.length} VM${users.length > 1 ? "s" : ""}`}
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating…" : "Create"}
            </Button>
          </div>
        </div>
      </form>
    </Drawer>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {error && <span className="block text-xs text-destructive">{error}</span>}
    </label>
  );
}
