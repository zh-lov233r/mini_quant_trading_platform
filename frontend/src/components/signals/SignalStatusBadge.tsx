import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import { signalStatus } from "@/i18n/messages/signals";

export function SignalStatusBadge({ status }: { status: string }) {
  const { locale } = useI18n();
  const tone = ["completed", "sent", "enabled"].includes(status) ? "success"
    : ["failed", "partial", "unknown"].includes(status) ? "warning"
    : ["queued", "running", "sending"].includes(status) ? "info" : "neutral";
  return <Badge tone={tone}>{signalStatus(status, locale)}</Badge>;
}
