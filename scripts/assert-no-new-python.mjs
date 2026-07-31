import { execFileSync } from "node:child_process";

const forbidden = [
  /(^|\/)requirements[^/]*\.txt$/i,
  /(^|\/)pyproject\.toml$/i,
  /(^|\/)Pipfile(?:\.lock)?$/,
  /(^|\/)poetry\.lock$/i,
  /(^|\/)uv\.lock$/i,
  /(^|\/)alembic\.ini$/i,
  /(^|\/)alembic\//i,
  /\.pyi?$/i,
  /\.pyc$/i,
];

function changedFiles() {
  if (process.env.NO_PYTHON_CHANGED_FILES) {
    return process.env.NO_PYTHON_CHANGED_FILES.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  }
  const base = process.env.NO_PYTHON_BASE;
  if (!base) {
    throw new Error("NO_PYTHON_BASE is required when NO_PYTHON_CHANGED_FILES is not provided");
  }
  return execFileSync("git", ["diff", "--name-only", "--diff-filter=ACMR", `${base}...HEAD`], {
    encoding: "utf8",
  })
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
}

const violations = changedFiles().filter((path) => forbidden.some((pattern) => pattern.test(path)));
if (violations.length > 0) {
  console.error("New or modified Python runtime files are forbidden in the TypeScript migration branch:");
  for (const path of violations) console.error(`- ${path}`);
  process.exit(1);
}
console.log("No new or modified Python runtime files detected.");
