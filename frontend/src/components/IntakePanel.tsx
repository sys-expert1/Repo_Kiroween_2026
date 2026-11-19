import { FolderGit2, Globe, HardDrive, Package } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiError } from "../services/api";
import type { Fixture, Repository, RepositorySource } from "../types/revival";
import { button, field, Notice, Panel, Spinner } from "./ui";

type Kind = "fixture" | "git" | "local";
const TABS: { id: Kind; label: string; icon: typeof Globe }[] = [
  { id: "fixture", label: "Demo fixture", icon: Package },
  { id: "git", label: "Git URL", icon: Globe },
  { id: "local", label: "Local path", icon: HardDrive },
];

interface Props {
  fixtures: Fixture[];
  onImport: (source: RepositorySource) => Promise<Repository>;
  onDone: () => void;
}

export function IntakePanel({ fixtures, onImport, onDone }: Props) {
  const [kind, setKind] = useState<Kind>("fixture");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent, override?: string) {
    e.preventDefault();
    const text = (override ?? value).trim();
    if (!text) {
      setError("Enter a value first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onImport(
        kind === "git" ? { git_url: text } : kind === "local" ? { local_path: text } : { fixture_id: text },
      );
      setValue("");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Add a repository" icon={FolderGit2}>
      <div role="tablist" aria-label="Repository source" className="mb-4 flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.id} type="button" role="tab" aria-selected={kind === t.id}
            onClick={() => { setKind(t.id); setError(null); setValue(""); }}
            className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${kind === t.id ? "border-haunt bg-haunt/15 text-haunt-soft" : "border-edge text-ink-2 hover:text-ink"}`}
          >
            <t.icon size={14} aria-hidden="true" /> {t.label}
          </button>
        ))}
      </div>

      {kind === "fixture" ? (
        fixtures.length === 0 ? (
          <p className="text-sm text-ink-2">No demo fixtures were found on the backend.</p>
        ) : (
          <ul className="space-y-2">
            {fixtures.map((f) => (
              <li key={f.id} className="flex items-center justify-between gap-3 rounded-md border border-edge bg-raised p-3">
                <div>
                  <p className="font-heading font-semibold">{f.name}</p>
                  <p className="text-xs text-ink-2">{f.description}</p>
                </div>
                <button type="button" className={button.primary} disabled={busy} onClick={(e) => submit(e, f.id)}>
                  Use
                </button>
              </li>
            ))}
          </ul>
        )
      ) : (
        <form onSubmit={submit} className="space-y-3" noValidate>
          <label className="block text-sm text-ink-2" htmlFor="source-value">
            {kind === "git" ? "Repository URL (https://, ssh:// or git@host:path)" : "Path on the machine running the backend"}
          </label>
          <input
            id="source-value" className={`${field} font-code`} value={value} autoComplete="off" spellCheck={false}
            placeholder={kind === "git" ? "https://github.com/owner/repo.git" : "/home/me/projects/legacy-app"}
            aria-invalid={error ? true : undefined} aria-describedby={error ? "source-error" : undefined}
            onChange={(e) => setValue(e.target.value)}
          />
          {kind === "local" && (
            <p className="text-xs text-ink-3">
              The local backend resolves this path, not the browser. It must be inside a directory listed in
              NECRO_ALLOWED_ROOTS, and paths with <code>..</code> or links that leave that directory are rejected.
            </p>
          )}
          {kind === "git" && (
            <p className="text-xs text-ink-3">
              The repository is cloned by the backend when a run starts. Its code is not executed during analysis.
            </p>
          )}
          <button type="submit" className={button.primary} disabled={busy}>
            {busy ? <Spinner label="Validating" /> : "Validate and import"}
          </button>
        </form>
      )}
      {error && (
        <div id="source-error" className="mt-3">
          <Notice tone="bad" title="Could not import" role="alert">{error}</Notice>
        </div>
      )}
    </Panel>
  );
}
