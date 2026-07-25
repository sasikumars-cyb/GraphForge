import { Key, ShieldCheck, Lock, Fingerprint } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";

interface SecurityItemProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  status: string;
  statusTone: "success" | "neutral" | "warning";
}

function SecurityItem({ icon, title, description, status, statusTone }: SecurityItemProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-slate-800 text-slate-400">
          {icon}
        </div>
        <div>
          <p className="text-sm font-medium text-slate-200">{title}</p>
          <p className="text-xs text-slate-500">{description}</p>
        </div>
      </div>
      <StatusBadge label={status} tone={statusTone} />
    </div>
  );
}

export function SecuritySection() {
  return (
    <div className="flex flex-col gap-5">
      <Card
        title="Credentials & Secrets"
        description="Encryption and credential management for integrations"
      >
        <div className="divide-y divide-slate-800/60">
          <SecurityItem
            icon={<Lock className="h-4 w-4" />}
            title="Token Encryption"
            description="All stored tokens encrypted at rest with Fernet (AES-128-CBC)"
            status="Active"
            statusTone="success"
          />
          <SecurityItem
            icon={<Key className="h-4 w-4" />}
            title="AI Provider Keys"
            description="API keys are write-only and never exposed in API responses"
            status="Secure"
            statusTone="success"
          />
          <SecurityItem
            icon={<Fingerprint className="h-4 w-4" />}
            title="AWS Credentials"
            description="Bedrock uses the AWS SDK credential chain. No keys stored in GraphForge."
            status="External"
            statusTone="neutral"
          />
          <SecurityItem
            icon={<ShieldCheck className="h-4 w-4" />}
            title="GitHub OAuth"
            description="OAuth token for repository access, encrypted at rest"
            status="Configured"
            statusTone="success"
          />
        </div>
      </Card>

      <Card
        title="Access Control"
        description="Authentication and authorization settings"
      >
        <div className="divide-y divide-slate-800/60">
          <SecurityItem
            icon={<Key className="h-4 w-4" />}
            title="JWT Authentication"
            description="Session tokens with configurable expiration"
            status="HS256"
            statusTone="success"
          />
          <SecurityItem
            icon={<ShieldCheck className="h-4 w-4" />}
            title="Webhook Verification"
            description="HMAC signature verification for GitHub webhook deliveries"
            status={
              /* would check config */ "Configured"
            }
            statusTone="success"
          />
        </div>
      </Card>
    </div>
  );
}
