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


/**
 * A failed changelog fetch used to be indistinguishable from a filter that
 * matched nothing: the catch set the notes to null, and an empty list always
 * rendered "No lines match the filter" - blaming a filter the reader had not
 * typed into, and hiding the actual reason.
 */
describe('RouterFirmwareModal release notes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getFirmwareStatus.mockResolvedValue(mockStatus);
  });

  it('reports why the notes could not be loaded instead of blaming the filter', async () => {
    api.getChangelog.mockRejectedValue(new Error('HTTP 404 from upgrade server'));

    render(<RouterFirmwareModal isOpen={true} onClose={vi.fn()} routerId={1} routerName="Core-Gateway" />);

    await waitFor(() => {
      expect(screen.getByText(/HTTP 404 from upgrade server/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/No lines match the filter/i)).toBeNull();
  });

  it('still blames the filter when notes did load but nothing matched', async () => {
    api.getChangelog.mockResolvedValue({ version: '7.16.1', notes: '*) bridge - fixed vlan filtering;' });

    render(<RouterFirmwareModal isOpen={true} onClose={vi.fn()} routerId={1} routerName="Core-Gateway" />);

    await waitFor(() => expect(screen.getByText(/bridge - fixed vlan filtering/)).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Filter release notes/i), {
      target: { value: 'zzzznomatch' },
    });
    expect(screen.getByText(/No lines match the filter/i)).toBeInTheDocument();
  });
});

/**
 * The navbar badge is fed by App's own firmware status, which is refreshed on a
 * five-minute slow poll. Switching the update channel inside this modal changes
 * the answer immediately - so until the modal hands the fresh status back, the
 * badge keeps advertising a version from a channel the router is no longer on.
 */
describe('RouterFirmwareModal status propagation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getFirmwareStatus.mockResolvedValue(mockStatus);
    api.getChangelog.mockResolvedValue({ version: '7.16.1', notes: 'note' });
  });

  it('hands the fresh status up when the update channel changes', async () => {
    const onStatusChange = vi.fn();
    const backToStable = {
      ...mockStatus,
      packages: {
        ...mockStatus.packages,
        channel: 'stable',
        latest_version: '7.15.2',
        update_available: false,
      },
    };
    api.setFirmwareChannel.mockResolvedValue(backToStable);

    render(
      <RouterFirmwareModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
        onStatusChange={onStatusChange}
      />
    );

    await waitFor(() => expect(screen.getAllByText('7.16.1').length).toBeGreaterThanOrEqual(1));

    fireEvent.change(await screen.findByDisplayValue('stable'), { target: { value: 'development' } });

    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith(
        expect.objectContaining({
          packages: expect.objectContaining({ update_available: false, latest_version: '7.15.2' }),
        })
      );
    });
  });
});
