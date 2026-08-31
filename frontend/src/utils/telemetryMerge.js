/**
 * Fold a telemetry frame into the user list held in React state.
 *
 * Extracted from App so it can be tested directly: it carries the identity
 * rules that decide how often the whole dashboard repaints, and those are worth
 * pinning down rather than eyeballing.
 *
 * The contract is that nothing changes identity unless its value changed:
 *
 *   - an unchanged device returns the same object
 *   - a user whose figures and devices are all unchanged returns the same object
 *   - a frame that moved nothing returns the *same array*, which lets React bail
 *     out of the update entirely
 *
 * Rebuilding every object each frame made React re-render, and the browser
 * repaint, every card once a second even on a completely idle network.
 */
export function mergeTelemetryIntoUsers(prevUsers, telemetryUsers) {
  if (!Array.isArray(prevUsers) || prevUsers.length === 0) return prevUsers;
  if (!Array.isArray(telemetryUsers) || telemetryUsers.length === 0) return prevUsers;

  const byUserId = new Map(telemetryUsers.map(u => [u.user_id, u]));
  let usersChanged = false;

  const nextUsers = prevUsers.map(user => {
    const live = byUserId.get(user.id);
    if (!live) return user;

    // Per-device live figures ride along in the same frame, so device rows
    // animate at the same cadence as the user totals.
    const perDevice = live.devices || {};
    let devicesChanged = false;

    const nextDevices = (user.devices || []).map(device => {
      const update = perDevice[device.id];
      if (!update) return device;
      const differs = Object.keys(update).some(key => device[key] !== update[key]);
      if (!differs) return device;
      devicesChanged = true;
      return { ...device, ...update };
    });

    const userDiffers =
      user.current_rate_in !== live.current_rate_in ||
      user.current_rate_out !== live.current_rate_out ||
      user.bytes_today_in !== live.bytes_in ||
      user.bytes_today_out !== live.bytes_out ||
      user.is_paused !== live.is_paused;

    if (!userDiffers && !devicesChanged) return user;

    usersChanged = true;
    return {
      ...user,
      current_rate_in: live.current_rate_in,
      current_rate_out: live.current_rate_out,
      bytes_today_in: live.bytes_in,
      bytes_today_out: live.bytes_out,
      is_paused: live.is_paused,
      devices: devicesChanged ? nextDevices : user.devices,
    };
  });

  return usersChanged ? nextUsers : prevUsers;
}
