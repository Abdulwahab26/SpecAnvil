// SpecAnvil for VS Code. Everything is computed by the local `specanvil` command; nothing leaves the machine.
'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { parseDrift, findReference, bySpec, message } = require('./lib/drift');

const DIAGRAM_KINDS = ['modules', 'deps', 'call', 'class', 'sequence', 'tectonic'];
const NEEDS_TARGET = new Set(['call', 'class', 'sequence']);
let diagnostics;
let output;
let timer;

function root() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder ? folder.uri.fsPath : undefined;
}

function run(args, { okCodes = [0] } = {}) {
  const cwd = root();
  if (!cwd) return Promise.reject(new Error('Open a folder first.'));
  const bin = vscode.workspace.getConfiguration('specanvil').get('path') || 'specanvil';
  return new Promise((resolve, reject) => {
    cp.execFile(bin, args, { cwd, maxBuffer: 32 * 1024 * 1024, timeout: 300000 }, (err, stdout, stderr) => {
      output.appendLine(`$ ${bin} ${args.join(' ')}`);
      if (stderr) output.appendLine(stderr.trim());
      if (err && err.code === 'ENOENT') {
        reject(new Error(`'${bin}' was not found. Install SpecAnvil or set "specanvil.path".`));
      } else if (err && !okCodes.includes(err.code)) {
        reject(new Error((stderr || stdout || err.message).trim().split('\n').slice(-3).join('\n')));
      } else {
        resolve(stdout);
      }
    });
  });
}

async function guarded(fn) {
  try {
    await fn();
  } catch (e) {
    vscode.window.showErrorMessage(`SpecAnvil: ${e.message}`);
  }
}

async function checkDrift(announce) {
  const { summary, issues } = parseDrift(await run(['drift', '--json']));
  diagnostics.clear();
  const levels = {
    error: vscode.DiagnosticSeverity.Error,
    warning: vscode.DiagnosticSeverity.Warning,
    information: vscode.DiagnosticSeverity.Information,
  };
  for (const [spec, list] of bySpec(issues)) {
    const file = path.join(root(), spec);
    let text = '';
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch (e) {
      continue;
    }
    const found = [];
    for (const issue of list) {
      const spots = findReference(text, issue.ref);
      for (const s of spots.length ? spots : [{ line: 0, start: 0, end: 0 }]) {
        const d = new vscode.Diagnostic(new vscode.Range(s.line, s.start, s.line, s.end), message(issue), levels[issue.severity]);
        d.source = 'SpecAnvil';
        d.code = issue.issue;
        found.push(d);
      }
    }
    diagnostics.set(vscode.Uri.file(file), found);
  }
  if (announce) vscode.window.showInformationMessage(`SpecAnvil drift: ${summary}`);
}

function showHtml(title, html) {
  const panel = vscode.window.createWebviewPanel('specanvil', title, vscode.ViewColumn.Beside, { enableScripts: true });
  panel.webview.html = html;
}

async function showReport() {
  const out = (await run(['report'])).trim().split('\n').pop();
  const file = path.isAbsolute(out) ? out : path.join(root(), out);
  showHtml('SpecAnvil report', fs.readFileSync(file, 'utf8'));
}

async function showDiagram() {
  const kind = await vscode.window.showQuickPick(DIAGRAM_KINDS, { placeHolder: 'Diagram kind' });
  if (!kind) return;
  let target = '';
  if (NEEDS_TARGET.has(kind)) {
    const editor = vscode.window.activeTextEditor;
    const word = editor && editor.document.getText(editor.document.getWordRangeAtPosition(editor.selection.active) || editor.selection);
    target = await vscode.window.showInputBox({ prompt: 'Symbol (name or path::Qualname)', value: word || '' });
    if (!target) return;
  }
  const file = path.join(os.tmpdir(), `specanvil-${kind}-${Date.now()}.html`);
  await run(['diagram', kind].concat(target ? [target] : [], ['-o', file]));
  showHtml(`SpecAnvil ${kind}${target ? ` ${target}` : ''}`, fs.readFileSync(file, 'utf8'));
  fs.unlink(file, () => {});
}

async function showChangeRisk() {
  const base = vscode.workspace.getConfiguration('specanvil').get('riskBase') || 'HEAD';
  const md = await run(['pr-comment', '--base', base]);
  const doc = await vscode.workspace.openTextDocument({ language: 'markdown', content: md });
  await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
}

function activate(context) {
  diagnostics = vscode.languages.createDiagnosticCollection('specanvil');
  output = vscode.window.createOutputChannel('SpecAnvil');
  const cmd = (name, fn) => context.subscriptions.push(vscode.commands.registerCommand(name, () => guarded(fn)));
  cmd('specanvil.checkDrift', () => checkDrift(true));
  cmd('specanvil.lockSpecs', async () => {
    await run(['drift', '--lock']);
    await checkDrift(true);
  });
  cmd('specanvil.showReport', showReport);
  cmd('specanvil.showDiagram', showDiagram);
  cmd('specanvil.showChangeRisk', showChangeRisk);
  cmd('specanvil.reindex', async () => {
    await run(['index', '--full']);
    await checkDrift(true);
  });
  context.subscriptions.push(diagnostics, output,
    vscode.workspace.onDidSaveTextDocument(() => {
      if (!vscode.workspace.getConfiguration('specanvil').get('checkDriftOnSave')) return;
      clearTimeout(timer);
      timer = setTimeout(() => checkDrift(false).catch((e) => output.appendLine(e.message)), 800);
    }));
  checkDrift(false).catch((e) => output.appendLine(e.message));
}

function deactivate() {
  clearTimeout(timer);
}

module.exports = { activate, deactivate };
