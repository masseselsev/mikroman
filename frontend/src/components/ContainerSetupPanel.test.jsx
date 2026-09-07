import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContainerSetupPanel } from './ContainerSetupPanel';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    containerSetupPlan: vi.fn(),
    containerSetupApply: vi.fn(),
    containerMigrateData: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({ lang: 'en', t: (k) => k }),
}));

const PLAN = {
  ok: true,
  steps: [
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
};

// The button carries a count of the steps that would still change something, so
// its text is "ctr_apply (2)" once a plan exists - match the prefix.
const applyButton = () => screen.getByText(/^ctr_apply/).closest('button');

beforeEach(() => {
  vi.clearAllMocks();
  api.containerSetupPlan.mockResolvedValue({ data: PLAN });
  api.containerSetupApply.mockResolvedValue({
    data: { ...PLAN, steps: PLAN.steps.map(s => ({ ...s, applied: true, action: 'done' })) },
  });
  api.containerMigrateData.mockResolvedValue({
    data: {
      database_bytes: 1048576,
      staged_path: '/data/mikroman-migration.db',
      destination: 'usb1-part1/mikroman_data/app.db',
      secret_key: 'included',
      next_steps: [
        'copy /data/mikroman-migration.db to the router as usb1-part1/mikroman_data/app.db',
        'copy /data/.secret_key to the router as usb1-part1/mikroman_data/.secret_key',
        'start the container, then stop this instance - only one of them may write',
      ],
    },
  });
});

describe('ContainerSetupPanel', () => {
  it('refuses to apply anything before a plan has been shown', () => {
    render(<ContainerSetupPanel routerId={1} config={{}} />);
    expect(screen.getByText('ctr_setup_title')).toBeTruthy();
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
  });

  it('sends a null interface when the forward field is cleared', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.change(container.querySelector('input[value="br.lan"]'), { target: { value: '' } });
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(api.containerSetupPlan).toHaveBeenCalled());
    expect(api.containerSetupPlan.mock.calls[0][1].expose_on_interface).toBe(null);
  });

  it('stages the migration and spells out the copy it cannot do itself', async () => {
    const { container } = render(<ContainerSetupPanel routerId={1} config={{}} />);
    fireEvent.click(screen.getByText('ctr_plan'));
    await waitFor(() => expect(api.containerSetupPlan).toHaveBeenCalled());
    fireEvent.click(screen.getByText('ctr_migrate'));
    await waitFor(() => expect(api.containerMigrateData).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toContain('1.0 MB'));
    // Not a silent half-success: the remaining copy steps are shown as a list.
    expect(container.textContent).toContain('copy /data/mikroman-migration.db');
    expect(container.textContent).toContain('only one of them may write');
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
