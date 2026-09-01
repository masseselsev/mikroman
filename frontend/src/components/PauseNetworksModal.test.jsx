import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen } from '../test/render';
import { PauseNetworksModal, isValidCidrOrIp, parseNetworksList } from './PauseNetworksModal';

describe('isValidCidrOrIp', () => {
  it('validates correct IPv4 CIDR and single IPs', () => {
    expect(isValidCidrOrIp('192.168.1.0/24')).toBe(true);
    expect(isValidCidrOrIp('10.0.0.0/8')).toBe(true);
    expect(isValidCidrOrIp('172.16.0.0/12')).toBe(true);
    expect(isValidCidrOrIp('192.168.88.1')).toBe(true);
    expect(isValidCidrOrIp('0.0.0.0/0')).toBe(true);
  });

  it('rejects invalid inputs', () => {
    expect(isValidCidrOrIp('')).toBe(false);
    expect(isValidCidrOrIp('999.1.1.1')).toBe(false);
    expect(isValidCidrOrIp('192.168.1.0/33')).toBe(false);
    expect(isValidCidrOrIp('not-an-ip')).toBe(false);
  });
});

describe('parseNetworksList', () => {
  it('parses comma and newline separated lists', () => {
    expect(parseNetworksList('192.168.1.0/24, 10.0.0.0/8\n172.16.0.0/12')).toEqual([
      '192.168.1.0/24',
      '10.0.0.0/8',
      '172.16.0.0/12',
    ]);
  });

  it('falls back to default RFC1918 subnets when empty', () => {
    expect(parseNetworksList('')).toEqual(['192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12']);
  });
});

describe('PauseNetworksModal', () => {
  it('renders configured networks and allows adding/removing', async () => {
    const onSave = vi.fn().mockResolvedValue({});
    const onClose = vi.fn();

    renderWithProviders(
      <PauseNetworksModal
        isOpen={true}
        onClose={onClose}
        currentNetworks="192.168.88.0/24, 10.10.0.0/16"
        onSave={onSave}
      />
    );

    expect(screen.getByText('192.168.88.0/24')).toBeInTheDocument();
    expect(screen.getByText('10.10.0.0/16')).toBeInTheDocument();

    // Add a new subnet
    const input = screen.getByPlaceholderText(/192.168.1.0\/24/i);
    fireEvent.change(input, { target: { value: '172.20.0.0/16' } });
    fireEvent.click(screen.getByText('Add'));

    expect(screen.getByText('172.20.0.0/16')).toBeInTheDocument();

    // Save
    fireEvent.click(screen.getByText('Save'));
    expect(onSave).toHaveBeenCalledWith('192.168.88.0/24, 10.10.0.0/16, 172.20.0.0/16');
  });
});

