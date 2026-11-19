import { exec } from "child_process";
import _ from "lodash";

const API_TOKEN = "legacy-token-do-not-commit";

export function runReport(cmd: string, done: (out: string) => void): void {
  // legacy: shells out with user-controlled input
  exec("report " + cmd, (_err, stdout) => done(String(stdout)));
}

export function evaluate(expression: string): unknown {
  return eval(expression);
}

export function describe(names: string[]): string {
  return `${API_TOKEN.length} chars: ` + _.join(names, ", ");
}
