import instances from './instances.js'

/**
 * Toggle SGR mouse tracking (DEC 1000/1002/1003/1006) at runtime on the Ink
 * instance bound to this stdout. No-op if no Ink instance is attached.
 *
 * Use this for in-session `/mouse on|off` toggles. The <AlternateScreen>
 * prop owns setup/teardown at mount/unmount; this function sidesteps the
 * full alt-screen re-entry so the toggle is flicker-free.
 *
 * Updates the instance's internal `altScreenMouseTracking` flag so the
 * resize / SIGCONT-resume / re-enter-alt paths respect the new state.
 *
 * Defaults to `process.stdout` — pass a specific stream for tests or
 * multi-output setups.
 */
export function setAltScreenMouseTracking(
  enabled: boolean,
  stdout: NodeJS.WriteStream = process.stdout
): void {
  instances.get(stdout)?.setAltScreenMouseTracking(enabled)
}
