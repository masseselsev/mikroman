import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContainerSetupPanel } from './ContainerSetupPanel';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    containerSetupPlan: vi.fn(),
    containerSetupApply: vi.fn(),
    containerStorage: vi.fn(),
    containerFormat: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({ lang: 'en', t: (k) => k }),
}));

const PLAN = {
  ok: true,
  steps: [
    { key: 'storage_dir', action: 'exists', detail: 'usb1-part1: ext4, 261.4 GB free of 465.8 GB' },
    { key: 'config', action: 'set', detail: 'layer-dir=usb1-part1/container-layers' },
    { key: 'bridge', action: 'create', detail: 'bridge bridge-containers' },
    { key: 'mount', action: 'exists', detail: 'usb1-part1/mikroman_data -> /data' },
    { key: 'env', action: 'skip', detail: 'none' },
  ],
  blockers: [],
};

const BLOCKED = {
  ok: false,
  steps: [{ key: 'storage_dir', action: 'blocked', detail: 'not a storage the router can see' }],
  blockers: ["storage_dir: 'nosuch' is not a storage the router can see."],
  storage: { ready: false, problems: ["'nosuch' is not a storage this router sees."] },
};

const DISKS = [
  {
    slot: 'usb1', type: 'hardware', fs: '-', mounted: false, read_only: false,
    formatting: false, is_partition: false, size_bytes: 500107862016, free_bytes: null,
    usable_for_containers: false, formatable: true, note: 'no filesystem',
  },
  {
    slot: 'usb1-part1', type: 'partition', fs: 'ext4', mounted: true, read_only: false,
    formatting: false, is_partition: true, mount_point: 'usb1-part1',
    size_bytes: 500105740288, free_bytes: 280677068800, used_pct: 43,
    usable_for_containers: true, formatable: false, note: 'in container use (usb1-part1)',
  },
];

const STORAGE_OK = { ready: true, matched_slot: 'usb1-part1', fs: 'ext4', problems: [], warnings: [], disks: DISKS };

// The button carries a count of the steps that would still change something, so
// its text is "ctr_apply (2)" once a plan exists - match the prefix.
const applyButton = () => screen.getByText(/^ctr_apply/).closest('button');

beforeEach(() => {
  vi.clearAllMocks();
  api.containerStorage.mockResolvedValue({ data: STORAGE_OK });
  api.containerSetupPlan.mockResolvedValue({ data: PLAN });
  api.containerSetupApply.mockResolvedValue({
    data: { ...PLAN, steps: PLAN.steps.map(s => ({ ...s, applied: true, action: 'done' })) },
  });
  api.containerFormat.mockResolvedValue({ data: { started: true, formatting: true } });
});

describe('ContainerSetupPanel', () => {
  it('asks the router what storage it has before offering a choice', async () => {
    render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    // The slots come from /disk, so the operator picks a device instead of
    // typing a path that might name one that cannot be written.
    expect(screen.getByText('usb1-part1')).toBeTruthy();
    expect(screen.getByText('usb1')).toBeTruthy();
  });

  it('labels why a disk cannot be used', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    // The whole device is listed with the reason it cannot be used, because
    // "format it" is the only way it ever becomes one. Unmounted is what the
    // row says first; a missing filesystem is the same advice from the other side.
    expect(screen.getByText('ctr_disk_unmounted')).toBeTruthy();
    // Free space is split across nodes (figure + label), so match the row.
    expect(container.textContent).toContain('261.4 GB');
    expect(container.textContent).toContain('ctr_disk_free');
  });

  it('says so when a mounted volume cannot be written to', async () => {
    api.containerStorage.mockResolvedValue({
      data: {
        ...STORAGE_OK,
        ready: false,
        problems: ["usb1-part1 is mounted read-only (ntfs); container layers and the database need writes."],
        disks: [{ ...DISKS[1], fs: 'ntfs', read_only: true, usable_for_containers: false }],
      },
    });
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    // A router that mounts NTFS read-only is a real deployment, and the plan
    // would otherwise fail mid-pull with a registry error.
    expect(screen.getByText('ctr_disk_readonly')).toBeTruthy();
    expect(container.textContent).toContain('read-only');
  });

  it('refuses to apply anything before a plan has been shown', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    expect(container.textContent).toContain('ctr_setup_title');
    expect(applyButton().disabled).toBe(true);
    expect(api.containerSetupPlan).not.toHaveBeenCalled();
  });

  it('renders every step the plan promised, and counts what would change', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(container.textContent).toContain('bridge-containers'));
    expect(container.textContent).toContain('layer-dir=usb1-part1/container-layers');
    // set + create = two writes; exists and skip are not counted.
    expect(applyButton().textContent).toContain('(2)');
  });

  it('keeps apply disabled when the router reported a blocker', async () => {
    api.containerSetupPlan.mockResolvedValue({ data: BLOCKED });
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(container.textContent).toContain('not a storage the router can see'));
    expect(applyButton().disabled).toBe(true);
    expect(api.containerSetupApply).not.toHaveBeenCalled();
  });

  it('applies and then reports what landed', async () => {
    const onDone = vi.fn();
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} onDone={onDone} />);
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(applyButton().disabled).toBe(false));
    fireEvent.click(applyButton());
    await waitFor(() => expect(api.containerSetupApply).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toContain('ctr_applied'));
    expect(onDone).toHaveBeenCalled();
    // Defaults are the values this deployment needs: USB storage, the standard
    // container net, and the port forward bound to the LAN interface.
    const sent = api.containerSetupApply.mock.calls[0][1];
    expect(sent.storage_dir).toBe('usb1-part1');
    expect(sent.subnet).toBe('172.17.0.0/24');
    expect(sent.expose_on_interface).toBe('br.lan');
    // No RAM cap by default: an unset ceiling is the behaviour of every install
    // today, and guessing one lower turns a slow app into a killed one.
    expect(sent.ram_high).toBe(null);
  });

  it('sends a null interface when the forward field is cleared', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.change(container.querySelector('input[value="br.lan"]'), { target: { value: '' } });
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(api.containerSetupPlan).toHaveBeenCalled());
    expect(api.containerSetupPlan.mock.calls[0][1].expose_on_interface).toBe(null);
  });

  it('passes a typed RAM ceiling through to the plan', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.change(container.querySelector('input[placeholder="—"]'), { target: { value: '768M' } });
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(api.containerSetupPlan).toHaveBeenCalled());
    expect(api.containerSetupPlan.mock.calls[0][1].ram_high).toBe('768M');
  });

  it('offers formatting only for a device not holding container state', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    // usb1-part1 is in use by the mount, so it is not offered; usb1 is.
    expect(container.textContent).toContain('ctr_format_available');
    expect(container.querySelector('option[value="usb1-part1"]')).toBeNull();
  });

  it('will not send a format until the slot name has been typed back', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    await waitFor(() => expect(api.containerStorage).toHaveBeenCalled());
    fireEvent.click(screen.getByText('ctr_format'));
    await waitFor(() => expect(container.textContent).toContain('ctr_format_confirm'));
    expect(screen.getByText('ctr_format_confirm').closest('button').disabled).toBe(true);
    expect(api.containerFormat).not.toHaveBeenCalled();

    fireEvent.change(container.querySelector('input[placeholder="usb1"]'), { target: { value: 'usb1' } });
    expect(screen.getByText('ctr_format_confirm').closest('button').disabled).toBe(false);
    fireEvent.click(screen.getByText('ctr_format_confirm'));
    await waitFor(() => expect(api.containerFormat).toHaveBeenCalled());
    const sent = api.containerFormat.mock.calls[0][1];
    expect(sent).toEqual({ slot: 'usb1', file_system: 'ext4', label: '', confirm: 'usb1' });
  });

  it('surfaces the router message when a command is refused mid-plan', async () => {
    api.containerSetupApply.mockRejectedValue(new Error('data_dir: no such command'));
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(applyButton().disabled).toBe(false));
    fireEvent.click(applyButton());
    await waitFor(() => expect(container.textContent).toContain('no such command'));
  });

  it('shows where the layers go today, which is the reason the panel exists', () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{ layer_dir: '', tmpdir: '' }} />);
    expect(container.textContent).toContain('(internal flash)');
  });
});
