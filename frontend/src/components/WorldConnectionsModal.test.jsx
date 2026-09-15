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
    expect(screen.getByText(/ISO: NL • 1 connections/i)).toBeInTheDocument();
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

  it('does not violate rules of hooks when toggling isOpen from false to true', () => {
    const { rerender } = render(<WorldConnectionsModal isOpen={false} onClose={vi.fn()} connections={mockGeoConnections} />);
    expect(screen.queryByText(/Active Connections World Map/i)).not.toBeInTheDocument();

    rerender(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);
    expect(screen.getByText(/Active Connections World Map/i)).toBeInTheDocument();

    rerender(<WorldConnectionsModal isOpen={false} onClose={vi.fn()} connections={mockGeoConnections} />);
    expect(screen.queryByText(/Active Connections World Map/i)).not.toBeInTheDocument();
  });

  it('automatically clusters close countries at zoom 1 and separates them on zoom in', () => {
    const closeConnections = [
      {
        id: '*1',
        dst_ip: '198.51.100.1',
        country_code: 'NL',
        country_name: 'Netherlands',
        lat: 52.1326,
        lng: 5.2913,
      },
      {
        id: '*2',
        dst_ip: '198.51.100.2',
        country_code: 'DE',
        country_name: 'Germany',
        lat: 51.1657,
        lng: 10.4515,
      },
    ];

    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={closeConnections} />);

    // At zoom 1 (100%), NL and DE cluster together
    const cluster = screen.getByTestId('cluster-DE-NL');
    expect(cluster).toBeInTheDocument();
    expect(cluster).toHaveTextContent(/2 countries/i);

    // Clicking cluster zooms in to expand
    fireEvent.click(cluster);

    // Zoom increases beyond 100%
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('supports mouse wheel zooming on map canvas', () => {
    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={mockGeoConnections} />);

    expect(screen.getByText('100%')).toBeInTheDocument();
    const svg = screen.getByLabelText('World Connections Map');

    // Wheel up (zoom in)
    fireEvent.wheel(svg.parentElement, { deltaY: -100, clientX: 500, clientY: 250 });
    expect(screen.getByText('125%')).toBeInTheDocument();

    // Wheel down (zoom out)
    fireEvent.wheel(svg.parentElement, { deltaY: 100, clientX: 500, clientY: 250 });
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('supports ultra-high zoom up to 2000% and completely unclusters adjacent small countries', () => {
    const adjacentConnections = [
      {
        id: '*1',
        dst_ip: '198.51.100.1',
        country_code: 'NL',
        country_name: 'Netherlands',
        lat: 52.37, // Amsterdam
        lng: 4.89,
      },
      {
        id: '*2',
        dst_ip: '198.51.100.2',
        country_code: 'BE',
        country_name: 'Belgium',
        lat: 50.85, // Brussels
        lng: 4.35,
      },
    ];

    render(<WorldConnectionsModal isOpen={true} onClose={vi.fn()} connections={adjacentConnections} />);

    // At zoom 1 (100%), NL and BE are clustered together because distance is ~4.5 SVG units
    const cluster = screen.getByTestId('cluster-BE-NL');
    expect(cluster).toBeInTheDocument();

    const svg = screen.getByLabelText('World Connections Map');

    // Zoom in multiple times with wheel to reach high zoom (>10x / 1000%)
    for (let i = 0; i < 15; i++) {
      fireEvent.wheel(svg.parentElement, { deltaY: -100, clientX: 512, clientY: 106 });
    }

    // Both NL and BE are now cleanly separated individual nodes
    expect(screen.queryByTestId('cluster-BE-NL')).not.toBeInTheDocument();
    expect(screen.getByTestId('map-node-NL')).toBeInTheDocument();
    expect(screen.getByTestId('map-node-BE')).toBeInTheDocument();
  });

  it('renders directional moving flow lines and origin gateway marker', () => {
    const customRouterLocation = {
      lat: 51.5074,
      lng: -0.1278,
      countryCode: 'GB',
      countryName: 'London Gateway',
      publicIP: '198.51.100.99',
    };

    render(
      <WorldConnectionsModal
        isOpen={true}
        onClose={vi.fn()}
        connections={mockGeoConnections}
        routerLocation={customRouterLocation}
      />
    );

    // Origin node rendered
    const originNode = screen.getByTestId('map-origin-node');
    expect(originNode).toBeInTheDocument();
    expect(originNode).toHaveTextContent(/London Gateway/i);

    // Flow lines to destinations rendered
    expect(screen.getByTestId('flow-line-US')).toBeInTheDocument();
    expect(screen.getByTestId('flow-line-NL')).toBeInTheDocument();
  });

  it('scales flow line thickness proportionally based on connection percentage share', () => {
    // 9 connections to US (90%), 1 connection to NL (10%)
    const weightedConnections = [
      ...Array.from({ length: 9 }, (_, i) => ({
        id: `*us-${i}`,
        protocol: 'tcp',
        dst_ip: `198.51.100.${10 + i}`,
        country_code: 'US',
        country_name: 'United States',
        lat: 37.0902,
        lng: -95.7129,
        orig_rate: 1000,
        repl_rate: 1000,
        total_bytes: 1000,
      })),
      {
        id: '*nl-1',
        protocol: 'tcp',
        dst_ip: '198.51.100.50',
        country_code: 'NL',
        country_name: 'Netherlands',
        lat: 52.1326,
        lng: 5.2913,
        orig_rate: 500,
        repl_rate: 500,
        total_bytes: 500,
      },
    ];

    render(
      <WorldConnectionsModal
        isOpen={true}
        onClose={vi.fn()}
        connections={weightedConnections}
      />
    );

    const usFlowGroup = screen.getByTestId('flow-line-US');
    const nlFlowGroup = screen.getByTestId('flow-line-NL');

    // Title includes percentage share
    expect(usFlowGroup.querySelector('title')?.textContent).toMatch(/United States \(US\): 9 \(90\.0%\)/i);
    expect(nlFlowGroup.querySelector('title')?.textContent).toMatch(/Netherlands \(NL\): 1 \(10\.0%\)/i);

    // Find the animated path elements
    const usPath = usFlowGroup.querySelector('.map-flow-line, .map-flow-line-selected');
    const nlPath = nlFlowGroup.querySelector('.map-flow-line, .map-flow-line-selected');

    expect(usPath).toBeInTheDocument();
    expect(nlPath).toBeInTheDocument();

    const usStrokeWidth = parseFloat(usPath.getAttribute('stroke-width'));
    const nlStrokeWidth = parseFloat(nlPath.getAttribute('stroke-width'));

    // US has 90% of connections, NL has 10% -> US stroke must be significantly thicker!
    expect(usStrokeWidth).toBeGreaterThan(nlStrokeWidth);
  });

  it('displays target badge when filtered to specific device or user instance', () => {
    const targetDevice = {
      type: 'device',
      id: 42,
      name: 'WIN-HOST-A',
      ip: '192.0.2.142',
    };

    render(
      <WorldConnectionsModal
        isOpen={true}
        onClose={vi.fn()}
        connections={mockGeoConnections}
        target={targetDevice}
      />
    );

    const targetBadge = screen.getByTestId('world-map-target-badge');
    expect(targetBadge).toBeInTheDocument();
    expect(targetBadge).toHaveTextContent('WIN-HOST-A');
  });

  it('displays router name in modal header and origin beacon for overall router statistics', () => {
    const routerGeo = {
      lat: 41.2995,
      lng: 69.2401,
      countryCode: 'UZ',
      countryName: 'Uzbekistan',
      publicIP: '198.51.100.22',
      routerName: 'Main-CCR-Gateway',
    };

    render(
      <WorldConnectionsModal
        isOpen={true}
        onClose={vi.fn()}
        connections={mockGeoConnections}
        routerLocation={routerGeo}
        routerName="Main-CCR-Gateway"
      />
    );

    // Header displays router badge with router name and country info
    const headerBadge = screen.getByTestId('world-map-target-badge');
    expect(headerBadge).toBeInTheDocument();
    expect(headerBadge).toHaveTextContent('Main-CCR-Gateway');
    expect(headerBadge).toHaveTextContent('Uzbekistan');

    // Origin beacon on map displays router name, not country name
    // Origin beacon on map has tooltip with router name but no on-canvas text badge (preventing overlap)
    const originNode = screen.getByTestId('map-origin-node');
    expect(originNode).toBeInTheDocument();
    expect(originNode).toHaveTextContent('Main-CCR-Gateway');
    expect(originNode.querySelector('text')).toBeNull();
  });
});


