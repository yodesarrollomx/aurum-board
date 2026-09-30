'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { publish, dataPaths } = require('../scripts/publicar-metricas.cjs');

const BASE = 'a'.repeat(40);
const HEAD = 'b'.repeat(40);
const MERGE = 'c'.repeat(40);
const BRANCH = 'bot/metricas-123-1';

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
    calls.push({ argv, input: options.input });
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
  const options = { cwd, env: { GITHUB_REPOSITORY: 'yodesarrollomx/aurum-board', GITHUB_RUN_ID: '123', GITHUB_RUN_ATTEMPT: '1' },
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
  assert.deepEqual(await publish(f.options), { changed: false });
  assert.equal(f.calls.length, 1);
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
