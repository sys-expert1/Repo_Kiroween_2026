import {
  BadgeCheck, CircleDashed, CircleSlash, CircleX, Hourglass, Info, ShieldCheck, Activity,
} from "lucide-react";

import { RUN_STATE_LABEL } from "../state/runState";
import type { RunStatus } from "../types/revival";
import { Badge } from "./ui";

/** Emerald "Verified" only when Consecration produced a passing verdict. Plain completion is blue. */
export function StatusChip({ status, verdict }: { status: RunStatus | null; verdict: "verified" | "failed" | null }) {
  if (!status) return <Badge tone="muted" icon={CircleDashed}>No run</Badge>;
  if (status === "COMPLETED" && verdict === "verified") return <Badge tone="ok" icon={ShieldCheck}>Verified</Badge>;
  switch (status) {
    case "QUEUED": return <Badge tone="info" icon={Hourglass}>{RUN_STATE_LABEL[status]}</Badge>;
    case "RUNNING": return <Badge tone="agent" icon={Activity}>{RUN_STATE_LABEL[status]}</Badge>;
    case "AWAITING_APPROVAL": return <Badge tone="warn" icon={BadgeCheck}>{RUN_STATE_LABEL[status]}</Badge>;
    case "COMPLETED": return <Badge tone="info" icon={Info}>{RUN_STATE_LABEL[status]}</Badge>;
    case "FAILED": return <Badge tone="bad" icon={CircleX}>{RUN_STATE_LABEL[status]}</Badge>;
    case "CANCELLED": return <Badge tone="muted" icon={CircleSlash}>{RUN_STATE_LABEL[status]}</Badge>;
  }
}
