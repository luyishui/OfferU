const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const ts = require("typescript");

const sourcePath = path.resolve(__dirname, "../src/lib/resumeText.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const tmp = path.join(os.tmpdir(), `resumeText-${Date.now()}.cjs`);
fs.writeFileSync(tmp, compiled, "utf8");
const helpers = require(tmp);

assert.deepEqual(
  helpers.splitBullets("• Surveyed 30 users\n• Wrote product insights"),
  ["Surveyed 30 users", "Wrote product insights"]
);

assert.deepEqual(
  helpers.splitBullets("1. Built prototype\n2. Validated workflow"),
  ["Built prototype", "Validated workflow"]
);

assert.deepEqual(
  helpers.splitBullets("Surveyed 30 users\nWrote product insights"),
  ["Surveyed 30 users", "Wrote product insights"]
);

assert.deepEqual(
  helpers.splitBullets("<ul><li>Built prototype</li><li>Validated workflow</li></ul>"),
  ["Built prototype", "Validated workflow"]
);

assert.equal(
  helpers.descriptionLinesToPlainText(["• Surveyed 30 users", "Wrote product insights"]),
  "Surveyed 30 users\nWrote product insights"
);

console.log("resume text helper tests passed");
