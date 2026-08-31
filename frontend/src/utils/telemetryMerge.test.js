import { describe, expect, it } from 'vitest';
import { mergeTelemetryIntoUsers } from './telemetryMerge';

/**
 * The identity rules here decide how often the dashboard repaints. A frame
 * arrives roughly once a second; rebuilding every object on each one made React
 * re-render and the browser repaint every card even on an idle network.
 *
 * So these tests assert on object identity (toBe) as much as on values.
 */

const baseUser = () => ({
  id: 1,
  name: 'Mark',
  current_rate_in: 0,
  current_rate_out: 0,
  bytes_today_in: 100,
  bytes_today_out: 50,
  is_paused: false,
  devices: [
    { id: 10, custom_name: 'Laptop', current_rate_in: 0, current_rate_out: 0, bytes_today_in: 10, bytes_today_out: 5 },
    { id: 11, custom_name: 'Phone', current_rate_in: 0, current_rate_out: 0, bytes_today_in: 20, bytes_today_out: 8 },
  ],
});

const frameFor = (overrides = {}, deviceOverrides = {}) => ([{
  user_id: 1,
  current_rate_in: 0,
  current_rate_out: 0,
  bytes_in: 100,
  bytes_out: 50,
  is_paused: false,
  devices: {},
  ...overrides,
  ...(Object.keys(deviceOverrides).length ? { devices: deviceOverrides } : {}),
}]);

describe('when nothing changed', () => {
  it('returns the very same array, so React can bail out of the update', () => {
    const users = [baseUser()];
    expect(mergeTelemetryIntoUsers(users, frameFor())).toBe(users);
  });

  it('keeps device object identity when a device figure is unchanged', () => {
    const users = [baseUser()];
    const before = users[0].devices[0];
    const result = mergeTelemetryIntoUsers(users, frameFor({}, {
      10: { current_rate_in: 0, current_rate_out: 0 },
    }));
    expect(result).toBe(users);
    expect(result[0].devices[0]).toBe(before);
  });
});

describe('when something changed', () => {
  it('produces a new user object for the user that moved', () => {
    const users = [baseUser()];
    const result = mergeTelemetryIntoUsers(users, frameFor({ current_rate_in: 4200 }));
    expect(result).not.toBe(users);
    expect(result[0]).not.toBe(users[0]);
    expect(result[0].current_rate_in).toBe(4200);
  });

  it('maps the telemetry field names onto the user shape', () => {
    // The frame says bytes_in/bytes_out; the card reads bytes_today_in/out.
    const result = mergeTelemetryIntoUsers([baseUser()], frameFor({ bytes_in: 999, bytes_out: 777 }));
    expect(result[0].bytes_today_in).toBe(999);
    expect(result[0].bytes_today_out).toBe(777);
  });

  it('replaces only the device that moved and keeps the others', () => {
    const users = [baseUser()];
    const untouched = users[0].devices[1];
    const result = mergeTelemetryIntoUsers(users, frameFor({}, {
      10: { current_rate_in: 5000 },
    }));
    expect(result[0].devices[0].current_rate_in).toBe(5000);
    expect(result[0].devices[1]).toBe(untouched);
  });

  it('rebuilds the user when only a device moved', () => {
    // The card renders its device rows, so a device change must reach it.
    const users = [baseUser()];
    const result = mergeTelemetryIntoUsers(users, frameFor({}, { 11: { current_rate_out: 12 } }));
    expect(result[0]).not.toBe(users[0]);
    expect(result[0].devices[1].current_rate_out).toBe(12);
  });

  it('tracks the pause state', () => {
    const result = mergeTelemetryIntoUsers([baseUser()], frameFor({ is_paused: true }));
    expect(result[0].is_paused).toBe(true);
  });
});

describe('users the frame does not mention', () => {
  it('are passed through untouched', () => {
    const users = [baseUser(), { ...baseUser(), id: 2, name: 'Kristina' }];
    const result = mergeTelemetryIntoUsers(users, frameFor({ current_rate_in: 1 }));
    expect(result[1]).toBe(users[1]);
  });
});

describe('degenerate input', () => {
  it('returns the input unchanged rather than throwing', () => {
    const users = [baseUser()];
    expect(mergeTelemetryIntoUsers(users, [])).toBe(users);
    expect(mergeTelemetryIntoUsers(users, null)).toBe(users);
    expect(mergeTelemetryIntoUsers(users, undefined)).toBe(users);
    expect(mergeTelemetryIntoUsers([], frameFor())).toEqual([]);
    expect(mergeTelemetryIntoUsers(null, frameFor())).toBeNull();
  });

  it('handles a user with no devices array', () => {
    const users = [{ id: 1, current_rate_in: 0, current_rate_out: 0, bytes_today_in: 0, bytes_today_out: 0, is_paused: false }];
    const result = mergeTelemetryIntoUsers(users, frameFor({ bytes_in: 0, bytes_out: 0 }));
    expect(result).toBe(users);
  });
});
