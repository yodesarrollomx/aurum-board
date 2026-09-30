'use strict';

// Publica los archivos ya generados; nunca extrae métricas ni llama a sus fuentes.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const SHA = /^[0-9a-f]{40}$/;
const COMPONENTS = ['SYS-MARKETING', 'GAS-MARKETING', 'SHEET-MARKETING'];

function execute(argv, options = {}) {
  const result = spawnSync(argv[0], argv.slice(1), {
    encoding: 'utf8', timeout: 120000, maxBuffer: 4 * 1024 * 1024,
    cwd: options.cwd, input: options.input,
  });
  if (result.error) throw new Error(`No se pudo ejecutar ${argv[0]} ${argv[1] || ''}.`);
  return { status: result.status, stdout: result.stdout || '' };
}

function dataPaths(porcelain) {
  return porcelain.split('\0').filter(Boolean).map(entry => {
    const state = entry.slice(0, 2);
    const file = entry.slice(3);
    if (/[RCU]/.test(state) || !(file === 'metrics.json' || file.startsWith('covers/'))) {
      throw new Error('El árbol contiene cambios ajenos a metrics.json/covers o un conflicto. No se publica.');
    }
    return file;
  });
}

async function publish(options = {}) {
  const cwd = options.cwd || process.cwd();
  const env = options.env || process.env;
  const exec = options.exec || execute;
  const now = options.now || Date.now;
  const sleep = options.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const log = options.log || console.log;
  const waitMs = options.waitMs === undefined ? 15 * 60 * 1000 : options.waitMs;
  const intervalMs = options.intervalMs === undefined ? 15000 : options.intervalMs;
  const repo = env.GITHUB_REPOSITORY;
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo || '') ||
      !/^\d+$/.test(env.GITHUB_RUN_ID || '') || !/^\d+$/.test(env.GITHUB_RUN_ATTEMPT || '')) {
    throw new Error('Se requiere el contexto de una corrida de GitHub Actions.');
  }

  function command(argv, allowed = [0], input) {
    const result = exec(argv, { cwd, input });
    if (!allowed.includes(result.status)) {
      // No imprimir stderr ni el cuerpo remoto: pueden contener datos o credenciales.
      throw new Error(`Falló ${argv[0]} ${argv[1] || ''} (salida ${result.status}).`);
    }
    return result.stdout;
  }
  function api(endpoint, method = 'GET', body) {
    const argv = ['gh', 'api', '--method', method, endpoint];
    if (body !== undefined) argv.push('--input', '-');
    const output = command(argv, [0], body === undefined ? undefined : JSON.stringify(body));
    return output.trim() ? JSON.parse(output) : null;
  }

  const changed = dataPaths(command(['git', 'status', '--porcelain=v1', '--untracked-files=all', '-z']));
  if (!changed.length) {
    log('Sin cambios de métricas o portadas.');
    return { changed: false };
  }
  const metrics = JSON.parse(fs.readFileSync(path.join(cwd, 'metrics.json'), 'utf8'));
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) {
    throw new Error('metrics.json debe contener un objeto válido.');
  }
  const base = command(['git', 'rev-parse', 'HEAD']).trim();
  if (!SHA.test(base)) throw new Error('No se pudo identificar el commit base.');
  const branch = `bot/metricas-${env.GITHUB_RUN_ID}-${env.GITHUB_RUN_ATTEMPT}`;
  const originalImpact = JSON.parse(fs.readFileSync(path.join(cwd, 'architecture-impact.json'), 'utf8'));
  if (typeof originalImpact.model_revision !== 'string' || !originalImpact.model_revision.trim()) {
    throw new Error('El manifiesto no declara la revisión del atlas.');
  }
  const impact = {
    model_revision: originalImpact.model_revision,
    proposal_id: 'CHG-METRICS-001',
    summary: `Actualizar métricas agregadas y portadas generadas; corrida ${env.GITHUB_RUN_ID}, intento ${env.GITHUB_RUN_ATTEMPT}. Se conservan contratos y fuentes.`,
    components: COMPONENTS,
    tests: ['JSON.parse de metrics.json antes de crear el commit.',
      'node --test tests/publicar-metricas.test.cjs',
      'Guard arquitectónico sobre base_sha y head_sha exactos antes del merge.'],
    rollback: 'Revertir mediante PR el commit de datos si la revisión confirma un error. Conservar historial y credenciales; no reejecutar escrituras en fuentes para deshacer una publicación.',
  };
  fs.writeFileSync(path.join(cwd, 'architecture-impact.json'), JSON.stringify(impact, null, 2) + '\n');
  command(['git', 'checkout', '-b', branch]);
  command(['git', 'config', 'user.name', 'yod-board-bot']);
  command(['git', 'config', 'user.email', 'actions@users.noreply.github.com']);
  command(['git', 'add', '--', ...changed, 'architecture-impact.json']);
  const staged = command(['git', 'diff', '--cached', '--name-only', '-z']).split('\0').filter(Boolean);
  if (!staged.includes('architecture-impact.json') || staged.some(file =>
    file !== 'architecture-impact.json' && file !== 'metrics.json' && !file.startsWith('covers/'))) {
    throw new Error('El commit incluiría archivos fuera del alcance de la actualización.');
  }
  command(['git', 'diff', '--cached', '--check']);
  command(['git', 'commit', '-m', `Actualizar métricas — corrida ${env.GITHUB_RUN_ID}`]);
  const head = command(['git', 'rev-parse', 'HEAD']).trim();
  if (!SHA.test(head) || head === base) throw new Error('No se generó un commit nuevo válido.');
  command(['git', 'push', '--set-upstream', 'origin', branch]);

  let pr;
  let merged = false;
  try {
    pr = api(`repos/${repo}/pulls`, 'POST', {
      title: `Actualizar métricas — corrida ${env.GITHUB_RUN_ID}`,
      head: branch, base: 'main',
      body: 'Actualización periódica de métricas agregadas y portadas generadas.\n\n'
        + 'Propuesta: CHG-METRICS-001. El productor comprueba JSON y limita los archivos. '
        + 'La integración espera el guard del atlas sobre el commit exacto y las comprobaciones obligatorias. '
        + 'No modifica las fuentes ni sus credenciales.\n\n'
        + 'Rollback: revertir este commit mediante PR si una revisión confirma un error; conservar el historial.',
    });
    if (!Number.isSafeInteger(pr && pr.number)) throw new Error('GitHub no devolvió un PR válido.');
    log(`PR #${pr.number} creado; pendiente de validación.`);
    const deadline = now() + waitMs;
    function listGuardRuns() {
      const response = api(`repos/${repo}/actions/workflows/arquitectura.yml/runs?event=workflow_dispatch&branch=${encodeURIComponent(branch)}&per_page=100`);
      if (!response || !Array.isArray(response.workflow_runs)) {
        throw new Error('No se pudo leer el historial del guard.');
      }
      return response.workflow_runs;
    }
    const previousRuns = new Set(listGuardRuns().map(run => run.id));
    api(`repos/${repo}/actions/workflows/arquitectura.yml/dispatches`, 'POST', {
      ref: branch, inputs: { base_sha: base, head_sha: head },
    });
    let guardRun;
    while (now() < deadline) {
      const matching = listGuardRuns().filter(run => !previousRuns.has(run.id)
        && run.head_sha === head && run.head_branch === branch && run.event === 'workflow_dispatch');
      if (matching.length > 1) throw new Error('Más de una corrida coincide con la solicitud; revisar antes de integrar.');
      guardRun = matching[0];
      if (guardRun && guardRun.status === 'completed') {
        if (guardRun.conclusion !== 'success') throw new Error('El guard del atlas terminó sin éxito.');
        break;
      }
      await sleep(intervalMs);
    }
    if (!guardRun || guardRun.status !== 'completed') throw new Error('Se agotó el tiempo de espera del guard.');

    let checksReady = false;
    while (now() < deadline) {
      const checks = JSON.parse(command(['gh', 'pr', 'checks', String(pr.number), '--repo', repo,
        '--required', '--json', 'name,bucket,workflow'], [0, 1, 8]));
      if (!Array.isArray(checks) || !checks.some(check => check.name === 'Arquitectura YOD')) {
        throw new Error('Falta configurar el check obligatorio Arquitectura YOD.');
      }
      if (checks.some(check => ['fail', 'cancel', 'skipping'].includes(check.bucket))) {
        throw new Error('Una comprobación obligatoria no terminó con éxito.');
      }
      if (checks.every(check => check.bucket === 'pass')) { checksReady = true; break; }
      await sleep(intervalMs);
    }
    if (!checksReady) throw new Error('Se agotó el tiempo de espera de los checks obligatorios.');
    const current = api(`repos/${repo}/pulls/${pr.number}`);
    if (current.state !== 'open' || current.draft || current.head.sha !== head || current.base.ref !== 'main') {
      throw new Error('El PR ya no coincide con el commit y destino validados.');
    }
    command(['gh', 'pr', 'merge', String(pr.number), '--repo', repo, '--merge', '--match-head-commit', head]);
    const confirmation = api(`repos/${repo}/pulls/${pr.number}`);
    if (!confirmation.merged || !SHA.test(confirmation.merge_commit_sha || '')) {
      throw new Error('GitHub no confirmó el merge; revisar si está en cola o requiere intervención.');
    }
    merged = true;
    const build = api(`repos/${repo}/pages/builds`, 'POST');
    if (!build || !['queued', 'building', 'built'].includes(build.status)) {
      throw new Error('Pages no confirmó la aceptación de la compilación.');
    }
    log(`PR #${pr.number} integrado. Compilación Pages solicitada; verificar su resultado antes de afirmar publicación.`);
    return { changed: true, pr: pr.number, head, merged: true, buildRequested: true };
  } catch (error) {
    const recovery = pr && pr.number
      ? ` PR #${pr.number}: ${merged ? 'merge confirmado; revisar Pages' : 'revisar su estado antes de continuar'}.`
      : ' Revisar la rama de la corrida antes de repetir la publicación.';
    throw new Error(error.message + recovery);
  }
}

if (require.main === module) {
  publish().catch(error => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { publish, dataPaths };
