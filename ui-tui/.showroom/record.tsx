import { rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { Writable } from 'node:stream'
import { fileURLToPath } from 'node:url'

import React from 'react'

import { Box, render, Text } from '@hermes/ink'

import { Panel } from '../src/components/branding.js'
import { MessageLine } from '../src/components/messageLine.js'
import type { Theme } from '../src/theme.js'
import { DEFAULT_THEME } from '../src/theme.js'
import type { Msg } from '../src/types.js'

const showroomRoot = dirname(fileURLToPath(import.meta.url))

class Capture extends Writable {
  buffer = ''
  isTTY = true
  columns: number
  rows: number

  constructor(cols: number, rows: number) {
    super()
    this.columns = cols
    this.rows = rows
  }

  override _write(chunk: any, _encoding: any, callback: any) {
    this.buffer += chunk.toString()
    callback()
  }
}

const COLS = 80
const ROWS = 16
const t = DEFAULT_THEME

const snap = async (node: React.ReactElement, settle = 120): Promise<string> => {
  const stdout = new Capture(COLS, ROWS) as unknown as NodeJS.WriteStream
  const inst = await render(node, { stdout, exitOnCtrlC: false, patchConsole: false })

  await new Promise(resolve => setTimeout(resolve, settle))
  inst.unmount()

  return (stdout as unknown as Capture).buffer
}

const Msg = (msg: Msg) => <MessageLine cols={COLS} msg={msg} t={t} />

const ToolPanel = ({ items, title, theme }: { items: string[]; theme: Theme; title: string }) => (
  <Box flexDirection="column" marginLeft={2}>
    <Box>
      <Text color={theme.color.bronze}>⚡ </Text>
      <Text bold color={theme.color.amber}>
        {title}
      </Text>
      <Text color={theme.color.dim}> ({items.length})</Text>
    </Box>
    {items.map((item, i) => (
      <Box key={i}>
        <Text color={theme.color.bronze}>{i === items.length - 1 ? '└─ ' : '├─ '}</Text>
        <Text color={theme.color.dim}>{item}</Text>
      </Box>
    ))}
  </Box>
)

const Tree = ({
  rows,
  theme
}: {
  rows: { branch: 'mid' | 'last'; cols: string[]; tone?: 'amber' | 'dim' | 'gold' | 'ok' }[]
  theme: Theme
}) => (
  <Box flexDirection="column" marginLeft={2}>
    {rows.map((row, i) => {
      const stem = row.branch === 'last' ? '└─ ' : '├─ '
      const tone =
        row.tone === 'gold'
          ? theme.color.gold
          : row.tone === 'amber'
            ? theme.color.amber
            : row.tone === 'ok'
              ? theme.color.ok
              : theme.color.dim

      return (
        <Box key={i}>
          <Text color={theme.color.bronze}>{stem}</Text>
          <Text color={tone}>{row.cols.join('  ')}</Text>
        </Box>
      )
    })}
  </Box>
)

const writeWorkflow = (name: string, workflow: Record<string, unknown>) => {
  const out = join(showroomRoot, 'workflows', `${name}.json`)
  writeFileSync(out, JSON.stringify(workflow, null, 2))
  console.log(`  wrote ${out}`)
}

const featureTour = async () => {
  const userPrompt = await snap(<Msg role="user" text="Build a focused plan for a safer gateway approval flow." />)

  const assistantPlan = await snap(
    <Msg
      role="assistant"
      text="I'll trace the gateway guards first, then patch the smallest boundary that keeps approval commands live while an agent is blocked."
    />
  )

  const toolTrail = await snap(
    <ToolPanel
      items={[
        'rg "approval.request" gateway/ tui_gateway/',
        'ReadFile gateway/run.py',
        'ReadFile gateway/platforms/base.py'
      ]}
      theme={t}
      title="tool trail"
    />
  )

  const assistantResult = await snap(
    <Msg
      role="assistant"
      text="Found the split guard. Bypass both queues only for approval commands; normal chat ordering stays intact."
    />
  )

  return {
    composer: 'ask hermes anything',
    timeline: [
      { ansi: userPrompt, at: 200, id: 'user-row', type: 'frame' },
      { ansi: assistantPlan, at: 1500, id: 'assistant-plan', type: 'frame' },
      { ansi: toolTrail, at: 2900, id: 'tool-trail', type: 'frame' },
      { at: 3200, duration: 1700, target: 'tool-trail', type: 'spotlight' },
      {
        at: 3400,
        duration: 1700,
        position: 'right',
        target: 'tool-trail',
        text: 'Real ui-tui MessageLine + Panel rendered to ANSI and replayed via xterm.js.',
        type: 'caption'
      },
      { ansi: assistantResult, at: 5400, id: 'assistant-result', type: 'frame' },
      { at: 6100, duration: 1300, target: 'assistant-result', type: 'highlight' },
      {
        at: 6300,
        duration: 1700,
        position: 'right',
        target: 'assistant-result',
        text: 'Captions, spotlights, and fades layer on top of real ANSI. Best of both.',
        type: 'caption'
      },
      { at: 8100, duration: 600, text: '/approve', type: 'compose' }
    ],
    title: 'Hermes TUI · Feature Tour',
    viewport: { cols: COLS, rows: ROWS }
  }
}

const subagentTrail = async () => {
  const userPrompt = await snap(<Msg role="user" text="Run tests, lint, and a Railway preview deploy in parallel." />)

  const plan = await snap(
    <Msg role="assistant" text="Spawning three subagents on the fan-out lane and watching their tool counts." />
  )

  const live = await snap(
    <Tree
      rows={[
        { branch: 'mid', cols: ['tests   running   12 tools   ⏱ 14.2s'], tone: 'amber' },
        { branch: 'mid', cols: ['lint    running    4 tools   ⏱ 14.2s'], tone: 'amber' },
        { branch: 'last', cols: ['deploy  queued     0 tools   ⏱  0.0s'], tone: 'dim' }
      ]}
      theme={t}
    />
  )

  const hot = await snap(
    <Tree
      rows={[
        { branch: 'mid', cols: ['tests   complete  18 tools   ⏱ 22.7s   ✓'], tone: 'ok' },
        { branch: 'mid', cols: ['lint    complete   6 tools   ⏱ 18.1s   ✓'], tone: 'ok' },
        { branch: 'last', cols: ['deploy  running    9 tools   ⏱  9.4s'], tone: 'gold' }
      ]}
      theme={t}
    />
  )

  const summary = await snap(
    <Msg role="assistant" text="All three landed: 24 tests pass, lint clean, preview at https://pr-128.railway.app." />
  )

  return {
    composer: 'spawn the deploy fan-out',
    timeline: [
      { ansi: userPrompt, at: 200, id: 'ask', type: 'frame' },
      { ansi: plan, at: 1100, id: 'plan', type: 'frame' },
      { ansi: live, at: 2100, id: 'live', type: 'frame' },
      { at: 2300, duration: 1500, target: 'live', type: 'spotlight' },
      {
        at: 2500,
        duration: 1700,
        position: 'right',
        target: 'live',
        text: 'Each subagent gets its own depth and tool budget; the dashboard tracks them live.',
        type: 'caption'
      },
      { ansi: hot, at: 4400, id: 'hot', type: 'frame' },
      { at: 4600, duration: 1300, target: 'hot', type: 'highlight' },
      {
        at: 4800,
        duration: 1700,
        position: 'right',
        target: 'hot',
        text: 'Completed runs collapse, hot lanes stay vivid — the eye tracks the live agent.',
        type: 'caption'
      },
      { ansi: summary, at: 6800, id: 'summary', type: 'frame' },
      {
        at: 7000,
        duration: 1700,
        position: 'right',
        target: 'summary',
        text: 'Subagent results stream back into the parent transcript as a single highlight.',
        type: 'caption'
      },
      { at: 8800, duration: 600, text: '/agents', type: 'compose' }
    ],
    title: 'Hermes TUI · Subagent Trail',
    viewport: { cols: COLS, rows: ROWS }
  }
}

const slashCommands = async () => {
  const slashEcho = (text: string) => snap(<Msg kind="slash" role="user" text={text} />)

  const skillsEcho = await slashEcho('/skills search vibe')
  const skillsResults = await snap(
    <Panel
      sections={[
        {
          rows: [
            ['anthropics/skills/frontend-design', '★ trusted'],
            ['openai/skills/skill-creator', '· official'],
            ['skills.sh/community/vibe-coding', '⚙ community']
          ]
        }
      ]}
      t={t}
      title="skills · search vibe"
    />,
    180
  )

  const modelEcho = await slashEcho('/model claude-4.6-sonnet')
  const modelSwitch = await snap(
    <Panel
      sections={[
        {
          rows: [
            ['from', 'gpt-5-codex'],
            ['to', 'claude-4.6-sonnet'],
            ['scope', 'this session']
          ]
        }
      ]}
      t={t}
      title="model switched"
    />,
    180
  )

  const agentsEcho = await slashEcho('/agents pause')
  const agentsStatus = await snap(
    <Panel
      sections={[
        {
          rows: [
            ['delegation', 'paused'],
            ['max children', '4'],
            ['running tasks', 'queued for resume']
          ]
        }
      ]}
      t={t}
      title="agents · paused"
    />,
    180
  )

  const helpEcho = await slashEcho('/help')
  const helpPanel = await snap(
    <Panel
      sections={[
        {
          items: ['/skills    search · install · inspect', '/model     switch model · pop picker'],
          title: 'Tools & Skills'
        },
        {
          items: [
            '/agents    spawn-tree dashboard',
            '/queue     queue prompt for next turn',
            '/steer     inject after next tool call'
          ],
          title: 'Session'
        },
        {
          items: ['/voice     toggle voice mode', '/details   thinking · tools · subagents · activity'],
          title: 'Configuration'
        }
      ]}
      t={t}
      title="(^_^)? Commands"
    />,
    220
  )

  return {
    composer: '',
    timeline: [
      { at: 200, duration: 700, text: '/skills search vibe', type: 'compose' },
      { ansi: skillsEcho, at: 1100, type: 'frame' },
      { at: 1100, duration: 200, text: '', type: 'compose' },
      { ansi: skillsResults, at: 1400, id: 'skills', type: 'frame' },
      {
        at: 1700,
        duration: 2000,
        position: 'right',
        target: 'skills',
        text: 'Typed /skills, hit return — same Panel the live TUI renders.',
        type: 'caption'
      },
      { at: 4000, duration: 700, text: '/model claude-4.6-sonnet', type: 'compose' },
      { ansi: modelEcho, at: 4900, type: 'frame' },
      { at: 4900, duration: 200, text: '', type: 'compose' },
      { ansi: modelSwitch, at: 5200, id: 'model', type: 'frame' },
      {
        at: 5500,
        duration: 1900,
        position: 'right',
        target: 'model',
        text: '/model swaps mid-session; transcript and cache stay intact.',
        type: 'caption'
      },
      { at: 7600, duration: 600, text: '/agents pause', type: 'compose' },
      { ansi: agentsEcho, at: 8400, type: 'frame' },
      { at: 8400, duration: 200, text: '', type: 'compose' },
      { ansi: agentsStatus, at: 8700, id: 'agents', type: 'frame' },
      {
        at: 9000,
        duration: 1800,
        position: 'right',
        target: 'agents',
        text: 'Same registry powers TUI, gateway, Telegram, Discord — one truth.',
        type: 'caption'
      },
      { at: 11000, duration: 400, text: '/help', type: 'compose' },
      { ansi: helpEcho, at: 11500, type: 'frame' },
      { at: 11500, duration: 200, text: '', type: 'compose' },
      { ansi: helpPanel, at: 11800, id: 'help', type: 'frame' }
    ],
    title: 'Hermes TUI · Slash Commands',
    viewport: { cols: COLS, rows: ROWS }
  }
}

const voiceMode = async () => {
  const vad = await snap(
    <ToolPanel
      items={['▮ ▮▮ ▮ ▮▮▮▮ ▮▮ ▮▮▮▮▮▮ ▮▮▮ ▮', 'rms 0.42 · 1.6s captured', 'auto-stop · silence 380ms']}
      theme={t}
      title="VAD · capturing"
    />
  )

  const transcript = await snap(<Msg role="user" text="what's in my inbox today and what needs a reply before noon?" />)

  const answer = await snap(
    <Msg
      role="assistant"
      text="Three threads need you before noon: vendor renewal, podcast intro feedback, and the design review at 11."
    />
  )

  const tts = await snap(
    <ToolPanel
      items={['voice 11labs · grace_v3', 'elapsed 4.6s · 2 chunks queued', 'ducking mic input']}
      theme={t}
      title="tts · playing"
    />
  )

  return {
    composer: 'ctrl+b to start recording',
    timeline: [
      { ansi: vad, at: 250, id: 'vad', type: 'frame' },
      { at: 600, duration: 1500, target: 'vad', type: 'spotlight' },
      {
        at: 800,
        duration: 1700,
        position: 'right',
        target: 'vad',
        text: 'Continuous loop: VAD detects silence, transcribes, restarts — no key holds.',
        type: 'caption'
      },
      { ansi: transcript, at: 2700, id: 'transcript', type: 'frame' },
      { at: 3400, duration: 1100, target: 'transcript', type: 'highlight' },
      {
        at: 3600,
        duration: 1700,
        position: 'right',
        target: 'transcript',
        text: 'Transcript flows straight into the composer with the standard ❯ user glyph.',
        type: 'caption'
      },
      { ansi: answer, at: 5500, id: 'answer', type: 'frame' },
      { ansi: tts, at: 6700, id: 'tts', type: 'frame' },
      {
        at: 7000,
        duration: 1700,
        position: 'right',
        target: 'tts',
        text: 'TTS auto-ducks the mic so the loop never echoes itself back.',
        type: 'caption'
      },
      { at: 8800, duration: 600, text: '/voice off', type: 'compose' }
    ],
    title: 'Hermes TUI · Voice Mode',
    viewport: { cols: COLS, rows: ROWS }
  }
}

// --- Static prompt mocks (no useInput, safe for snap()) ---

const ApprovalPromptStatic = ({
  command,
  description,
  selected = 0,
  theme
}: {
  command: string
  description: string
  selected?: number
  theme: Theme
}) => {
  const labels = ['Allow once', 'Allow this session', 'Always allow', 'Deny']
  const lines = command.split('\n').slice(0, 5)

  return (
    <Box borderColor={theme.color.warn} borderStyle="double" flexDirection="column" paddingX={1}>
      <Text bold color={theme.color.warn}>
        ⚠ approval required · {description}
      </Text>

      <Box flexDirection="column" paddingLeft={1}>
        {lines.map((line, i) => (
          <Text color={theme.color.cornsilk} key={i}>
            {line || ' '}
          </Text>
        ))}
      </Box>

      <Text />

      {labels.map((label, i) => (
        <Text key={label}>
          <Text bold={i === selected} color={i === selected ? theme.color.warn : theme.color.dim} inverse={i === selected}>
            {i === selected ? '▸ ' : '  '}
            {i + 1}. {label}
          </Text>
        </Text>
      ))}

      <Text color={theme.color.dim}>↑/↓ select · Enter confirm · 1-4 quick pick · Ctrl+C deny</Text>
    </Box>
  )
}

const ClarifyPromptStatic = ({
  choices,
  question,
  selected = 0,
  theme
}: {
  choices: string[]
  question: string
  selected?: number
  theme: Theme
}) => (
  <Box flexDirection="column">
    <Text bold>
      <Text color={theme.color.amber}>ask</Text>
      <Text color={theme.color.cornsilk}> {question}</Text>
    </Text>

    {[...choices, 'Other (type your answer)'].map((c, i) => (
      <Text key={i}>
        <Text bold={i === selected} color={i === selected ? theme.color.label : theme.color.dim} inverse={i === selected}>
          {i === selected ? '▸ ' : '  '}
          {i + 1}. {c}
        </Text>
      </Text>
    ))}

    <Text color={theme.color.dim}>
      ↑/↓ select · Enter confirm · 1-{choices.length + 1} quick pick · Esc cancel
    </Text>
  </Box>
)

const ModelPickerStatic = ({
  currentModel,
  items,
  selected = 0,
  stage,
  theme
}: {
  currentModel: string
  items: string[]
  selected?: number
  stage: 'model' | 'provider'
  theme: Theme
}) => (
  <Box borderStyle="double" borderColor={theme.color.amber} flexDirection="column" paddingX={1} width={50}>
    <Text bold color={theme.color.amber} wrap="truncate-end">
      {stage === 'provider' ? 'Select Provider' : 'Select Model'}
    </Text>

    <Text color={theme.color.dim} wrap="truncate-end">
      {stage === 'provider' ? `Current model: ${currentModel}` : currentModel}
    </Text>

    <Text color={theme.color.label} wrap="truncate-end">
      {' '}
    </Text>

    <Text color={theme.color.dim}>{' '}</Text>

    {items.map((item, i) => (
      <Text
        bold={i === selected}
        color={i === selected ? theme.color.amber : theme.color.dim}
        inverse={i === selected}
        key={item}
        wrap="truncate-end"
      >
        {i === selected ? '▸ ' : '  '}
        {i + 1}. {item}
      </Text>
    ))}

    <Text color={theme.color.dim}>{' '}</Text>
    <Text color={theme.color.dim}>persist: session · g toggle</Text>
    <Text color={theme.color.dim}>↑/↓ select · Enter choose · 1-9,0 quick · Esc/q cancel</Text>
  </Box>
)

const interactivePrompts = async () => {
  // User asks for something that triggers approval
  const userAsk = await snap(
    <Msg role="user" text="Run npm install express in the project root." />
  )

  const assistantExplains = await snap(
    <Msg
      role="assistant"
      text="I'll install express. The package manager needs approval — here's the command."
    />
  )

  // Approval prompt
  const approval = await snap(
    <ApprovalPromptStatic
      command={'npm install express\nadded 58 packages in 3.2s\n\n+ express@5.1.0'}
      description="install dependency"
      theme={t}
    />,
    180
  )

  // After approval, user asks something ambiguous
  const userClarify = await snap(
    <Msg role="user" text="Deploy this to staging." />
  )

  const assistantAsks = await snap(
    <Msg role="assistant" text="Which environment should I target?" />
  )

  // Clarify prompt
  const clarify = await snap(
    <ClarifyPromptStatic
      choices={['staging-us-east', 'staging-eu-west', 'staging-ap-south']}
      question="Which region?"
      theme={t}
    />,
    180
  )

  const confirmResult = await snap(
    <Panel
      sections={[
        {
          rows: [
            ['target', 'staging-us-east'],
            ['branch', 'main'],
            ['preview', 'https://pr-128.railway.app']
          ]
        }
      ]}
      t={t}
      title="deployment queued"
    />,
    180
  )

  return {
    composer: 'deploy this to staging',
    timeline: [
      { ansi: userAsk, at: 200, id: 'ask', type: 'frame' },
      { ansi: assistantExplains, at: 1200, id: 'explain', type: 'frame' },
      { ansi: approval, at: 2600, id: 'approval', type: 'frame' },
      { at: 2900, duration: 1500, target: 'approval', type: 'spotlight' },
      {
        at: 3100,
        duration: 2000,
        position: 'right',
        target: 'approval',
        text: 'Approval prompts gate dangerous commands. Four options: allow once, session, always, deny.',
        type: 'caption'
      },
      { at: 5400, duration: 400, text: '1', type: 'compose' },
      { at: 5900, duration: 500, text: '', type: 'compose' },
      { ansi: userClarify, at: 6600, id: 'clarify-ask', type: 'frame' },
      { ansi: assistantAsks, at: 7600, id: 'clarify-reply', type: 'frame' },
      { ansi: clarify, at: 8800, id: 'clarify', type: 'frame' },
      { at: 9100, duration: 1500, target: 'clarify', type: 'spotlight' },
      {
        at: 9300,
        duration: 2000,
        position: 'right',
        target: 'clarify',
        text: 'Clarify prompts handle ambiguous requests — numbered choices or free text.',
        type: 'caption'
      },
      { at: 11600, duration: 400, text: '1', type: 'compose' },
      { ansi: confirmResult, at: 12200, id: 'result', type: 'frame' },
      { at: 12500, duration: 1300, target: 'result', type: 'highlight' }
    ],
    title: 'Hermes TUI · Interactive Prompts',
    viewport: { cols: COLS, rows: ROWS }
  }
}

const modelPicker = async () => {
  const userAsk = await snap(
    <Msg role="user" text="Switch to Claude." />
  )

  const assistantReply = await snap(
    <Msg role="assistant" text="Opening the model picker — pick a provider first, then a model." />
  )

  // Provider selection stage
  const providers = await snap(
    <ModelPickerStatic
      currentModel="gpt-5-codex"
      items={[
        'OpenAI · 8 models',
        'Anthropic · 6 models',
        'Google · 5 models',
        'OpenRouter · 42 models',
        'xAI · 3 models'
      ]}
      selected={1}
      stage="provider"
      theme={t}
    />,
    180
  )

  // Model selection stage
  const models = await snap(
    <ModelPickerStatic
      currentModel="Anthropic"
      items={[
        'claude-opus-4',
        'claude-sonnet-4',
        'claude-sonnet-3.7',
        'claude-haiku-3.5',
        'claude-sonnet-3.5'
      ]}
      selected={1}
      stage="model"
      theme={t}
    />,
    180
  )

  const result = await snap(
    <Panel
      sections={[
        {
          rows: [
            ['from', 'gpt-5-codex'],
            ['to', 'claude-sonnet-4'],
            ['scope', 'this session']
          ]
        }
      ]}
      t={t}
      title="model switched"
    />,
    180
  )

  return {
    composer: '',
    timeline: [
      { at: 200, duration: 500, text: '/model', type: 'compose' },
      { ansi: userAsk, at: 900, id: 'ask', type: 'frame' },
      { ansi: assistantReply, at: 1800, id: 'reply', type: 'frame' },
      { ansi: providers, at: 3000, id: 'providers', type: 'frame' },
      { at: 3300, duration: 1800, target: 'providers', type: 'spotlight' },
      {
        at: 3500,
        duration: 2000,
        position: 'right',
        target: 'providers',
        text: 'Provider stage: pick from authenticated backends. Shows model count per provider.',
        type: 'caption'
      },
      { at: 5600, duration: 300, text: '2', type: 'compose' },
      { ansi: models, at: 6200, id: 'models', type: 'frame' },
      { at: 6500, duration: 1800, target: 'models', type: 'spotlight' },
      {
        at: 6700,
        duration: 2000,
        position: 'right',
        target: 'models',
        text: 'Model stage: scrollable list with ▸ selection. Number keys for quick pick.',
        type: 'caption'
      },
      { at: 9000, duration: 300, text: '2', type: 'compose' },
      { ansi: result, at: 9600, id: 'result', type: 'frame' },
      { at: 9900, duration: 1300, target: 'result', type: 'highlight' },
      {
        at: 10100,
        duration: 1700,
        position: 'right',
        target: 'result',
        text: 'Model swap mid-session. Transcript and cache stay intact.',
        type: 'caption'
      }
    ],
    title: 'Hermes TUI · Model Picker',
    viewport: { cols: COLS, rows: ROWS }
  }
}

const main = async () => {
  console.log('recording workflows…')

  // Wipe the workflows dir so deleted/renamed scenes don't linger.
  const workflowsDir = join(showroomRoot, 'workflows')

  for (const file of [
    'feature-tour.json',
    'subagent-trail.json',
    'slash-commands.json',
    'voice-mode.json',
    'interactive-prompts.json',
    'model-picker.json',
    'ink-frames.json'
  ]) {
    try {
      rmSync(join(workflowsDir, file))
    } catch {
      /* ignore */
    }
  }

  writeWorkflow('feature-tour', await featureTour())
  writeWorkflow('subagent-trail', await subagentTrail())
  writeWorkflow('slash-commands', await slashCommands())
  writeWorkflow('voice-mode', await voiceMode())
  writeWorkflow('interactive-prompts', await interactivePrompts())
  writeWorkflow('model-picker', await modelPicker())

  console.log('done')
}

void main().catch(error => {
  console.error(error)
  process.exit(1)
})
