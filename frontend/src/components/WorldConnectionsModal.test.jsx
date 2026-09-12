import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorldConnectionsModal } from './WorldConnectionsModal';

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({
    t: (k) => {
      const trans = {
        world_map_title: 'Active Connections World Map',
        world_map_countries: 'countries',
        connections_count: 'connections',
        search_connections_placeholder: 'Search IP, domain, device...',
        world_map_no_geo: 'No active external connections with geographic location detected',
        world_map_active_endpoints: 'Active Endpoints',
        close: 'Close',
        no_connections_found: 'No connections found',
      };
      return trans[k] || k;
    },
    lang: 'en',
  }),
}));

vi.mock('../context/SpeedUnitContext', () => ({
  useSpeedUnit: () => ({
    speedUnit: 'bits',
  }),
}));

const mockGeoConnections = [
  {
    id: '*1',
    protocol: 'tcp',
    src_ip: '192.168.88.50',
    dst_ip: '142.250.190.46',
    domain: 'youtube.com',
    country_code: 'US',
    country_name: 'United States',
    flag_emoji: '🇺🇸',
    lat: 37.0902,
    lng: -95.7129,
    orig_rate: 1000000,
    repl_rate: 8000000,
    orig_bytes: 50000,
    repl_bytes: 400000,
    total_bytes: 450000,
  },
  {
    id: '*2',
    protocol: 'tcp',
    src_ip: '192.168.88.50',
    dst_ip: '91.108.56.100',
    domain: 'telegram.org',
    country_code: 'NL',
    country_name: 'Netherlands',
    flag_emoji: '🇳🇱',
    lat: 52.1326,
    lng: 5.2913,
    orig_rate: 500000,
    repl_rate: 1500000,
    orig_bytes: 20000,
    repl_bytes: 80000,
    total_bytes: 100000,
  },
  {
    id: '*3',
    protocol: 'udp',
    src_ip: '192.168.88.50',
    dst_ip: '192.168.88.1',
    country_code: 'LOCAL',
    country_name: 'Local Network',
    flag_emoji: '🏠',
    lat: null,
    lng: null,
    orig_rate: 1000,
    repl_rate: 1000,
    total_bytes: 2000,
  },
];

describe('WorldConnectionsModal', () => {
  it('renders correctly with external geo connections and excludes local network', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);

    expect(screen.getByText(/Active Connections World Map/i)).toBeInTheDocument();
    // 2 countries (US, NL) - LOCAL excluded
    expect(screen.getByText(/2 countries • 2 connections/i)).toBeInTheDocument();

    // Map nodes rendered
    expect(screen.getByTestId('map-node-US')).toBeInTheDocument();
    expect(screen.getByTestId('map-node-NL')).toBeInTheDocument();
  });

  it('switches active country on click of node or ranking card', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);

    // Click Netherlands node
    const nlNode = screen.getByTestId('map-node-NL');
    fireEvent.click(nlNode);

    // Detail card now shows Netherlands
    expect(screen.getByText('ISO: NL • 1 connections')).toBeInTheDocument();
    expect(screen.getByText('telegram.org')).toBeInTheDocument();
  });

  it('filters countries using search box', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);

    const searchInput = screen.getByPlaceholderText(/Search IP, domain, device/i);
    fireEvent.change(searchInput, { target: { value: 'youtube' } });

    // US remains visible because youtube.com matches
    expect(screen.getByTestId('map-node-US')).toBeInTheDocument();
    // NL is filtered out
    expect(screen.queryByTestId('map-node-NL')).not.toBeInTheDocument();
  });

  it('displays empty state message when no connections have geographic coordinates', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={[]} />);

    expect(
      screen.getByText(/No active external connections with geographic location detected/i)
    ).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn();
    render(<WorldConnectionsModal isOpen={true} onClose={onClose} connections={mockGeoConnections} />);

    const closeBtn = screen.getByTitle('Close');
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('supports interactive zoom controls', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);

    expect(screen.getByText('100%')).toBeInTheDocument();
    const zoomInBtn = screen.getByTitle('Zoom in');
    fireEvent.click(zoomInBtn);

    expect(screen.getByText('150%')).toBeInTheDocument();
    const resetBtn = screen.getByTitle('Reset view');
    expect(resetBtn).toBeInTheDocument();

    fireEvent.click(resetBtn);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('pins unknown UN connections to Antarctica', () => {
    const unConnections = [
      {
        id: '*99',
        src_ip: '192.168.88.50',
        dst_ip: '198.51.100.99',
        country_code: 'UN',
        country_name: 'Global Internet',
        lat: 20.0, // even if payload reports 20, UI overrides to -78
        lng: 0.0,
      },
    ];
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={unConnections} />);

    const unNode = screen.getByTestId('map-node-UN');
    expect(unNode).toBeInTheDocument();

    // In project(lat, lng):
    // x = ((0 + 180)/360) * 1000 = 500
    // y = ((90 - (-78))/180) * 500 = 466.67
    const circle = unNode.querySelector('circle[stroke="#ffffff"]');
    expect(circle).toBeInTheDocument();
    expect(circle.getAttribute('cx')).toBe('500');
    expect(Number(circle.getAttribute('cy'))).toBeCloseTo(466.67, 1);
  });
});


