// Minimal ambient declarations so the package needs no @types/node.
declare const process: {
  argv: string[];
  env: Record<string, string | undefined>;
  exitCode?: number;
  stdout: { write(chunk: string): unknown };
  stderr: { write(chunk: string): unknown };
};
