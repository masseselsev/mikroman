import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, renderWithProviders as render, screen, waitFor } from '../test/render';
import React from 'react';
import RouterFirmwareModal from './RouterFirmwareModal';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getFirmwareStatus: vi.fn(),
    checkFirmwareUpdates: vi.fn(),
    setFirmwareChannel: vi.fn(),
    getChangelog: vi.fn(),
    upgradeRouterFirmware: vi.fn(),
    upgradeBootloader: vi.fn(),
    getRouters: vi.fn(),
  },
}));

const mockStatus = {
  router_id: 1,
  router_name: 'Core-Gateway',
  packages: {
    installed_version: '7.15.2',
    latest_version: '7.16.1',
    channel: 'stable',
    status: 'New version is available',
    update_available: true,
  },
  routerboard: {
    is_routerboard: true,
    model: 'RB5009UG+S+IN',
    serial_number: 'HF809ABC',
    current_firmware: '7.15.2',
    upgrade_firmware: '7.16.1',
    firmware_available: true,
  },
  checked_at: new Date().toISOString(),
};

describe('RouterFirmwareModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getFirmwareStatus.mockResolvedValue(mockStatus);
    api.getChangelog.mockResolvedValue({
      version: '7.16.1',
      notes: '*) bridge - fixed vlan filtering;\n*) wifi - added mlo roaming;\n*) lte - stability update;',
    });
    api.upgradeRouterFirmware.mockResolvedValue({
      status: 'rebooting',
      message: 'Upgrade initiated',
    });
  });

  it('renders package and bootloader versions with upgrade badge', async () => {
    render(
      <RouterFirmwareModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      expect(screen.getAllByText('7.15.2').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('7.16.1').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText(/RB5009UG\+S\+IN/i)).toBeInTheDocument();
    });
  });

  it('enforces exact name match confirmation before upgrade button unlocks', async () => {
    render(
      <RouterFirmwareModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    const upgradeBtn = await screen.findByRole('button', { name: /upgrade & reboot/i });
    expect(upgradeBtn).toBeDisabled();

    const input = screen.getByPlaceholderText(/Type "Core-Gateway" to confirm/i);
    fireEvent.change(input, { target: { value: 'WrongName' } });
    expect(upgradeBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: 'Core-Gateway' } });
    expect(upgradeBtn).not.toBeDisabled();

    fireEvent.click(upgradeBtn);
    await waitFor(() => {
      expect(api.upgradeRouterFirmware).toHaveBeenCalledWith(1, {
        confirm_name: 'Core-Gateway',
        stage_bootloader: true,
      });
    });
  });

  it('sizes the update-channel select so its text is not clipped at a 30px height', async () => {
    // Regression: the box overrode `height` to 30px but kept the `.form-select`
    // class's default `padding: 10px 14px`, which needs ~38px to fit a line of
    // text - the option text rendered cut off at the top of the box.
    render(
      <RouterFirmwareModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      const select = screen.getByDisplayValue('stable');
      expect(select.style.height).toBe('30px');
      // Padding shrunk to match the 30px box; line-height fills the
      // remaining content height so the text is vertically centred rather
      // than clipped.
      expect(select.style.padding).toBe('0px 36px 0px 10px');
      expect(select.style.lineHeight).toBe('28px');
    });
  });

  it('filters changelog entries with search query', async () => {
    render(
      <RouterFirmwareModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/bridge - fixed vlan filtering/i)).toBeInTheDocument();
      expect(screen.getByText(/wifi - added mlo roaming/i)).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/filter release notes/i);
    fireEvent.change(searchInput, { target: { value: 'wifi' } });

    expect(screen.getByText(/wifi - added mlo roaming/i)).toBeInTheDocument();
    expect(screen.queryByText(/bridge - fixed vlan filtering/i)).not.toBeInTheDocument();
  });
});

