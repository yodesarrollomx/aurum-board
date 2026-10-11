'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { publish, dataPaths } = require('../scripts/publicar-metricas.cjs');

const BASE = 'a'.repeat(40);
const HEAD = 'b'.repeat(40);
const MERGE = 'c'.repeat(40);
const BRANCH = 'bot/metricas-123-1';
const ACTIONS_TOKEN = 'synthetic-actions-token';
const APP_TOKEN = 'synthetic-app-token';

function fixture(t, changes = {}) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'aurum-publicar-'));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(path.join(cwd, 'metrics.json'), '{"series":[]}');
  fs.writeFileSync(path.join(cwd, 'architecture-impact.json'), '{"model_revision":"2026-09-30.1"}');
  const calls = [];
  const logs = [];
  let rev = 0;
  let reads = 0;
  let runReads = 0;
  let clock = 0;
  const goodRun = { id: 15, event: 'workflow_dispatch', head_sha: HEAD, head_branch: BRANCH, status: 'completed', conclusion: 'success' };
  const result = value => ({ status: 0, stdout: typeof value === 'string' ? value : JSON.stringify(value) });
  const exec = (argv, options) => {
    calls.push({ argv, input: options.input, env: options.env });
    if (changes.override) {
      const custom = changes.override(argv, options, calls);
      if (custom !== undefined) return custom;
    }
    if (argv[0] === 'git') {
      if (argv[1] === 'status') return result(changes.status === undefined ? ' M metrics.json\0?? covers/nueva foto.jpg\0' : changes.status);
      if (argv[1] === 'rev-parse') return result(rev++ ? HEAD : BASE);
      if (argv.includes('--name-only')) return result('metrics.json\0covers/nueva foto.jpg\0architecture-impact.json\0');
      return result('');
    }
    if (argv[0] === 'gh' && argv[1] === 'api') {
      const endpoint = argv[4];
      if (endpoint.endsWith('/dispatches')) return result('');
      if (endpoint.includes('/runs?')) return result({ workflow_runs: runReads++ ? (changes.runs || [goodRun]) : (changes.previousRuns || []) });
      if (endpoint.endsWith('/pages/builds')) return result({ status: changes.buildStatus || 'queued' });
      if (endpoint.endsWith('/pulls')) return result({ number: 42 });
      if (endpoint.endsWith('/pulls/42')) return result(reads++ ? { merged: true, merge_commit_sha: MERGE } : {
        state: 'open', draft: false, head: { sha: changes.currentHead || HEAD }, base: { ref: 'main' },
      });
    }
    if (argv[0] === 'gh' && argv[1] === 'pr') {
      if (argv[2] === 'checks') return result(changes.checks || [{ name: 'Arquitectura YOD', bucket: 'pass', workflow: 'Arquitectura YOD' }]);
      if (argv[2] === 'merge') return result('');
    }
    throw new Error('Comando inesperado en el doble: ' + argv.join(' '));
  };
  const options = { cwd, env: { GITHUB_REPOSITORY: 'yodesarrollomx/aurum-board', GITHUB_RUN_ID: '123', GITHUB_RUN_ATTEMPT: '1',
    GH_TOKEN: ACTIONS_TOKEN, PR_CREATION_TOKEN: APP_TOKEN },
    exec, now: () => clock, sleep: async ms => { clock += ms; }, intervalMs: 10, waitMs: 30, log: msg => logs.push(msg) };
  return { cwd, options, calls, logs };
}

function mutations(calls, verb) { return calls.filter(call => call.argv[0] === 'gh' && call.argv[2] === verb); }

test('solo la rama de corrida recibe el commit; PR, dispatch exacto, merge y solicitud Pages en orden', async t => {
  const f = fixture(t);
  const result = await publish(f.options);
  assert.equal(result.head, HEAD);
  assert.equal(result.buildRequested, true);
  assert.deepEqual(f.calls.find(c => c.argv[1] === 'push').argv, ['git', 'push', '--set-upstream', 'origin', BRANCH]);
  assert.deepEqual(f.calls.find(c => c.argv[1] === 'add').argv, ['git', 'add', '--', 'metrics.json', 'covers/nueva foto.jpg', 'architecture-impact.json']);
  assert.ok(!f.calls.some(c => c.argv.includes('--admin') || c.argv.includes('--auto') || c.argv.join(' ').includes('[skip ci]')));
  const dispatch = f.calls.find(c => c.argv[4] && c.argv[4].endsWith('/dispatches'));
  assert.deepEqual(JSON.parse(dispatch.input), { ref: BRANCH, inputs: { base_sha: BASE, head_sha: HEAD } });
  const merge = mutations(f.calls, 'merge')[0];
  assert.equal(merge.argv.at(-1), HEAD);
  const checkIndex = f.calls.findIndex(c => c.argv[2] === 'checks');
  const mergeIndex = f.calls.indexOf(merge);
  const pagesIndex = f.calls.findIndex(c => c.argv[4] && c.argv[4].endsWith('/pages/builds'));
  assert.ok(checkIndex < mergeIndex && mergeIndex < pagesIndex);
  const impact = JSON.parse(fs.readFileSync(path.join(f.cwd, 'architecture-impact.json'), 'utf8'));
  assert.equal(impact.proposal_id, 'CHG-METRICS-001');
  assert.deepEqual(impact.components, ['SYS-MARKETING', 'GAS-MARKETING', 'SHEET-MARKETING']);
});

test('sin cambios no crea commits ni ejecuta GitHub', async t => {
  const f = fixture(t, { status: '' });
  delete f.options.env.PR_CREATION_TOKEN;
  delete f.options.env.GH_TOKEN;
  assert.deepEqual(await publish(f.options), { changed: false });
  assert.equal(f.calls.length, 1);
});

test('detectar cambios permite decidir si hace falta token sin modificar archivos ni llamar GitHub', async t => {
  for (const status of ['', ' M metrics.json\0']) {
    const f = fixture(t, { status });
    delete f.options.env.PR_CREATION_TOKEN;
    delete f.options.env.GH_TOKEN;
    const originalImpact = fs.readFileSync(path.join(f.cwd, 'architecture-impact.json'), 'utf8');
    assert.deepEqual(await publish({ ...f.options, checkOnly: true }), { changed: Boolean(status) });
    assert.deepEqual(f.calls.map(c => c.argv[1]), ['status']);
    assert.equal(fs.readFileSync(path.join(f.cwd, 'architecture-impact.json'), 'utf8'), originalImpact);
  }
});

test('la CLI de detección ejecuta git sin heredar PR_CREATION_TOKEN', t => {
  const f = fixture(t);
  const trace = path.join(f.cwd, 'environment.json');
  const fakeGit = path.join(f.cwd, 'git');
  fs.writeFileSync(fakeGit, `#!${process.execPath}\n`
    + 'require("node:fs").writeFileSync("environment.json", JSON.stringify({GH_TOKEN:process.env.GH_TOKEN,PR_CREATION_TOKEN:process.env.PR_CREATION_TOKEN}));\n'
    + 'process.stdout.write(" M metrics.json\\0");\n', { mode: 0o700 });
  const result = spawnSync(process.execPath, [path.resolve(__dirname, '../scripts/publicar-metricas.cjs'), '--check-changes'], {
    cwd: f.cwd, encoding: 'utf8', timeout: 10000,
    env: { ...process.env, ...f.options.env, PATH: f.cwd + path.delimiter + process.env.PATH },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'changed=true\n');
  assert.deepEqual(JSON.parse(fs.readFileSync(trace, 'utf8')), { GH_TOKEN: ACTIONS_TOKEN });
});

test('credenciales ausentes o compartidas impiden mutaciones locales y remotas', async t => {
  for (const credentials of [
    { PR_CREATION_TOKEN: undefined },
    { PR_CREATION_TOKEN: '  ' },
    { GH_TOKEN: undefined },
    { PR_CREATION_TOKEN: ACTIONS_TOKEN },
  ]) {
    const f = fixture(t);
    Object.assign(f.options.env, credentials);
    const originalImpact = fs.readFileSync(path.join(f.cwd, 'architecture-impact.json'), 'utf8');
    await assert.rejects(publish(f.options), /token|credencial/i);
    assert.deepEqual(f.calls.map(c => c.argv[1]), ['status']);
    assert.equal(fs.readFileSync(path.join(f.cwd, 'architecture-impact.json'), 'utf8'), originalImpact);
  }
});

test('sólo POST pulls recibe la App; git, guard, checks, merge y Pages conservan Actions', async t => {
  const f = fixture(t);
  await publish(f.options);
  const appCalls = f.calls.filter(c => c.env && c.env.GH_TOKEN === APP_TOKEN);
  assert.equal(appCalls.length, 1);
  assert.deepEqual(appCalls[0].argv.slice(0, 5), ['gh', 'api', '--method', 'POST', 'repos/yodesarrollomx/aurum-board/pulls']);
  for (const call of f.calls) {
    assert.equal(call.env.PR_CREATION_TOKEN, undefined);
    assert.equal(call.env.GH_TOKEN, call === appCalls[0] ? APP_TOKEN : ACTIONS_TOKEN);
    assert.ok(!call.argv.join(' ').includes(APP_TOKEN));
    assert.ok(!(call.input || '').includes(APP_TOKEN));
  }
  assert.equal(f.options.env.GH_TOKEN, ACTIONS_TOKEN);
  assert.equal(f.options.env.PR_CREATION_TOKEN, APP_TOKEN);
  assert.ok(!f.logs.join('\n').includes(APP_TOKEN));
});

test('fallo al crear PR no expone tokens ni continúa con guard o merge', async t => {
  const f = fixture(t, { override: argv => argv[4] && argv[4].endsWith('/pulls')
    ? { status: 1, stdout: APP_TOKEN, stderr: APP_TOKEN } : undefined });
  await assert.rejects(publish(f.options), error => {
    assert.ok(!error.message.includes(APP_TOKEN));
    return /Falló gh api/.test(error.message);
  });
  assert.equal(mutations(f.calls, 'merge').length, 0);
  assert.ok(!f.calls.some(c => c.argv[4] && c.argv[4].endsWith('/dispatches')));
});

test('rechaza archivos ajenos y renombres antes de publicar', () => {
  assert.throws(() => dataPaths(' M index.html\0'), /ajenos/);
  assert.throws(() => dataPaths('R  covers/a.jpg\0index.html\0'), /ajenos/);
});

test('JSON inválido impide crear rama o PR', async t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.cwd, 'metrics.json'), '{bad');
  await assert.rejects(publish(f.options));
  assert.equal(f.calls.length, 1);
});

test('rechaza staging ajeno aunque el status inicial fuera correcto', async t => {
  const f = fixture(t, { override: argv => argv.includes('--name-only')
    ? { status: 0, stdout: 'metrics.json\0architecture-impact.json\0index.html\0' } : undefined });
  await assert.rejects(publish(f.options), /fuera del alcance/);
  assert.ok(!f.calls.some(c => c.argv[1] === 'commit' || c.argv[1] === 'push'));
});

test('un dispatch exitoso de otro commit no habilita el merge y vence la espera', async t => {
  const f = fixture(t, { runs: [{ id: 1, event: 'workflow_dispatch', head_sha: BASE, head_branch: BRANCH, status: 'completed', conclusion: 'success' }] });
  await assert.rejects(publish(f.options), /tiempo de espera.*PR #42/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
  assert.equal(f.calls.filter(c => c.argv[4] && c.argv[4].endsWith('/dispatches')).length, 1);
});

test('fallo del guard deja el PR pendiente sin merge', async t => {
  const f = fixture(t, { runs: [{ id: 1, event: 'workflow_dispatch', head_sha: HEAD, head_branch: BRANCH, status: 'completed', conclusion: 'failure' }] });
  await assert.rejects(publish(f.options), /guard.*sin éxito.*PR #42/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
});

test('una corrida antigua del mismo commit no sustituye la solicitud actual', async t => {
  const run = { id: 1, event: 'workflow_dispatch', head_sha: HEAD, head_branch: BRANCH, status: 'completed', conclusion: 'success' };
  const f = fixture(t, { previousRuns: [run], runs: [run] });
  await assert.rejects(publish(f.options), /tiempo de espera/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
});

test('dos corridas nuevas coincidentes requieren revisión', async t => {
  const run = { event: 'workflow_dispatch', head_sha: HEAD, head_branch: BRANCH, status: 'completed', conclusion: 'success' };
  const f = fixture(t, { runs: [{ ...run, id: 1 }, { ...run, id: 2 }] });
  await assert.rejects(publish(f.options), /Más de una corrida/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
});

test('checks obligatorios pendientes esperan hasta el límite', async t => {
  const f = fixture(t, { checks: [{ name: 'Arquitectura YOD', bucket: 'pass' }, { name: 'otro', bucket: 'pending' }] });
  await assert.rejects(publish(f.options), /tiempo de espera.*checks.*PR #42/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
});

test('rollup inicialmente vacío espera hasta recibir todos los checks obligatorios', async t => {
  for (const empty of ['', '[]']) {
    let reads = 0;
    const f = fixture(t, { override: argv => argv[2] === 'checks' && reads++ === 0
      ? { status: 1, stdout: empty } : undefined });
    const result = await publish(f.options);
    assert.equal(result.merged, true);
    assert.equal(f.calls.filter(c => c.argv[2] === 'checks').length, 2);
  }
});

test('rollup vacío persistente vence sin merge ni Pages', async t => {
  const f = fixture(t, { override: argv => argv[2] === 'checks'
    ? { status: 1, stdout: '' } : undefined });
  await assert.rejects(publish(f.options), /tiempo de espera.*checks.*PR #42/);
  assert.equal(f.calls.filter(c => c.argv[2] === 'checks').length, 3);
  assert.equal(mutations(f.calls, 'merge').length, 0);
  assert.ok(!f.calls.some(c => c.argv[4] && c.argv[4].endsWith('/pages/builds')));
});

test('rollup inválido no se considera aprobado', async t => {
  for (const stdout of ['null', '{}', '[{}]']) {
    const f = fixture(t, { override: argv => argv[2] === 'checks'
      ? { status: 0, stdout } : undefined });
    await assert.rejects(publish(f.options));
    assert.equal(mutations(f.calls, 'merge').length, 0);
  }
});

test('check fallido o ausente impide merge', async t => {
  for (const checks of [[{ name: 'Arquitectura YOD', bucket: 'fail' }], []]) {
    const f = fixture(t, { checks });
    await assert.rejects(publish(f.options), /obligatori/);
    assert.equal(mutations(f.calls, 'merge').length, 0);
  }
});

test('un cambio de head posterior a la validación impide merge', async t => {
  const f = fixture(t, { currentHead: BASE });
  await assert.rejects(publish(f.options), /ya no coincide.*PR #42/);
  assert.equal(mutations(f.calls, 'merge').length, 0);
});

test('rechazo de revisión o protección no se reintenta ni usa bypass', async t => {
  const f = fixture(t, { override: argv => argv[2] === 'merge' ? { status: 1, stdout: '' } : undefined });
  await assert.rejects(publish(f.options), /Falló gh pr.*PR #42/);
  assert.equal(mutations(f.calls, 'merge').length, 1);
  assert.ok(!f.calls.some(c => c.argv[4] && c.argv[4].endsWith('/pages/builds')));
});

test('un Pages rechazado conserva el diagnóstico de merge y nunca afirma publicación', async t => {
  const f = fixture(t, { buildStatus: 'errored' });
  await assert.rejects(publish(f.options), /Pages.*merge confirmado/);
  assert.ok(!f.logs.some(message => message.includes('Compilación Pages solicitada')));
});
