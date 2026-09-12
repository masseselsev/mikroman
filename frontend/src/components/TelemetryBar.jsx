import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { useSpeedUnit } from '../context/SpeedUnitContext';
import { SpeedTestBadge } from './SpeedTestBadge';
import { api } from '../api/client';
import { formatFrequency, formatSpeed } from '../utils/formatters';
import { buildLookupUrl } from '../utils/ipLookup';
import { smoothAreaPath, smoothLinePath } from '../utils/sparkline';
import {
  Cpu,
  HardDrive,
  Thermometer,
  ArrowDown,
  ArrowUp,
  Sliders,
  X,
  Check,
  Network,
  Users,
  Globe
} from 'lucide-react';

/** How many telemetry ticks each sparkline remembers (~1 tick/second). */
const HISTORY_LENGTH = 60;

function pushHistory(previous, value) {
  if (value == null || Number.isNaN(value)) return previous;
  const next = [...previous, value];
  return next.length > HISTORY_LENGTH ? next.slice(next.length - HISTORY_LENGTH) : next;
}

/** Longest sub-line that fits a tile before the grid's width floor clips it. */
const SUB_LINE_FITS = 24;

/**
 * A tile sub-line that may not fit.
 *
 * Tiles clip their sub-lines, so a long processor name or a provider with a
 * verbose legal name would simply vanish mid-word with nothing to say that
 * anything had been cut. Past the width that fits, the text is marked with a
 * dotted underline - the conventional "there is a footnote here" cue - and
 * carries the full string as its tooltip. Short lines are left completely
 * unmarked, so the underline always means something is hidden.
 */
function Footnote({ text, children }) {
  const clipped = text.length > SUB_LINE_FITS;
  return (
    <span className={clipped ? 'footnote' : undefined} title={clipped ? text : undefined}>
      {children ?? text}
    </span>
  );
}

/**
 * Minimal inline sparkline. Drawn from values already arriving over the
 * telemetry socket, so it costs no extra request and needs no charting
 * dependency.
 *
 * The curve is a monotone cubic spline rather than a straight polyline: at one
 * sample every few seconds the raw line is visibly angular, and the smoothing
 * is arithmetic done once per tick, not a filter applied every frame. See
 * utils/sparkline for why monotone specifically - it cannot draw a peak that
 * the data does not contain.
 */
function Sparkline({ values, color, max }) {
  if (!values || values.length < 2) {
    return <div style={{ height: 18 }} />;
  }
  const width = 100;
  const height = 18;
  const peak = max != null ? max : Math.max(...values, 1);
  const scale = peak > 0 ? peak : 1;
  const step = width / (values.length - 1);

  const points = values.map((v, i) => ({
    x: i * step,
    y: height - Math.min(v / scale, 1) * height,
  }));

  const linePath = smoothLinePath(points);
  const areaPath = smoothAreaPath(points, height);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         style={{ width: '100%', height: 18, display: 'block' }} aria-hidden="true">
      {areaPath && <path d={areaPath} fill={color} opacity="0.14" />}
      <path d={linePath} fill="none" stroke={color} strokeWidth="1.5"
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/**
 * The public address, as a link to an external lookup service.
 *
 * Exactly one service, chosen in Settings. An earlier version offered a menu
 * when several were enabled, which failed twice over: it hung off a ten-pixel
 * line inside a tile whose lines are clipped with `overflow: hidden`, so the
 * menu was invisible - and it asked the reader to make a choice they had no
 * reason to care about at the moment of clicking.
 *
 * The href is built through buildLookupUrl, which refuses anything that is not
 * http(s); a template that fails renders as plain text rather than as a link.
 */
function PublicIpLink({ ip, service, t }) {
  const url = service ? buildLookupUrl(service.url_template, ip) : null;
  if (!url) return <span>↗ {ip}</span>;

  return (
    <a
      className="ip-lookup-link"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={e => e.stopPropagation()}
      title={t('ip_lookup_open', { service: service.name })}
    >
      ↗ {ip}
    </a>
  );
}

/**
 * One compact telemetry tile: label, primary value, a secondary detail, and an
 * optional sparkline. Replaces eight near-identical blocks of inline markup.
 *
 * `sub` accepts either a single line or an array of them, so a tile can carry
 * several related facts (the WAN tile shows the interface address, the public
 * address and the operator) without each caller inventing its own layout.
 * Falsy entries are dropped, which keeps the call sites free of conditionals.
 *
 * The value sits on the same line as the icon and label, pushed to the far
 * right, rather than on a line of its own below them - the tile used to
 * spend two lines (caption, then a large figure) on what a single row says
 * just as clearly, which is what let the tiles shrink a quarter narrower
 * without feeling cramped. `label` is optional: the WAN tile drops it
 * entirely, since its globe icon already says what the row is about and the
 * text was the tightest-fitting thing in the row.
 */
function Tile({ icon, tone, label, value, sub, history, historyMax, onClick, title, valueSize = 'var(--fs-md)' }) {
  const subLines = (Array.isArray(sub) ? sub : [sub]).filter(Boolean);

  return (
    <div
      className={`tile${onClick ? ' is-clickable' : ''}`}
      onClick={onClick}
      title={title}
    >
      <div className="tile-head">
        <span className="tile-icon" style={{ color: tone }}>{icon}</span>
        {label ? <span className="section-label truncate">{label}</span> : null}
        <span className="tile-value truncate" style={{ fontSize: valueSize, color: tone }}>
          {value}
        </span>
      </div>

      {history ? (
        <Sparkline values={history} color={tone} max={historyMax} />
      ) : null}

      {subLines.map((line, i) => (
        <div key={i} className="tile-sub truncate">{line}</div>
      ))}
    </div>
  );
}

/**
 * @param onNavigate  Optional. Called with a tab id when a tile is clicked.
 *                    A tile states a fact; the tab is where that fact can be
 *                    investigated, so the tile is the natural way in - CPU, RAM
 *                    and temperature lead to Router Health, and the client count
 *                    leads to the users and their traffic.
 */
export function TelemetryBar({ router, activeRouter, interfaces = [], onNavigate }) {
  // The speed test acts on a specific router record, not on the telemetry frame.
  const activeRouterId = activeRouter?.id;
  const { t } = useI18n();
  const { speedUnit } = useSpeedUnit();
  const [modalOpen, setModalOpen] = useState(false);
  const [modalTab, setModalTab] = useState('wan');
  const [availableIfaces, setAvailableIfaces] = useState([]);
  const [selectedIfaces, setSelectedIfaces] = useState([]);
  const [ignoredDiscoveryIfaces, setIgnoredDiscoveryIfaces] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Rolling history for the sparklines, fed from the telemetry socket itself.
  const [rxHistory, setRxHistory] = useState([]);
  const [txHistory, setTxHistory] = useState([]);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [memHistory, setMemHistory] = useState([]);
  const [tempHistory, setTempHistory] = useState([]);
  const [tempThreshold, setTempThreshold] = useState(null);

  // A sparkline history belongs to one router, so switching routers has to
  // empty it. The buffer holds up to 60 samples and the traffic sparklines
  // auto-scale to the largest value they contain, so carrying it across a
  // switch does two visible kinds of damage: the previous router's curve stays
  // on screen for a full minute, and its peak rescales the axis - a router
  // idling at 60 Kbps next to one that peaked at 900 Mbps draws as a flat line
  // at the bottom of the tile.
  //
  // The socket hook blanks the frame on the same change, but that cannot do
  // the clearing on its own: the push effect below returns early on a null
  // frame instead of resetting. Hence a separate effect keyed on the router
  // id, declared first so a reset and the new router's first sample landing in
  // the same commit happen in that order.
  useEffect(() => {
    setRxHistory([]);
    setTxHistory([]);
    setCpuHistory([]);
    setMemHistory([]);
    setTempHistory([]);
  }, [activeRouterId]);

  useEffect(() => {
    if (!router) return;
    setRxHistory(prev => pushHistory(prev, router.wan_rx_bps));
    setTxHistory(prev => pushHistory(prev, router.wan_tx_bps));
    setCpuHistory(prev => pushHistory(prev, router.cpu_load));
    if (router.total_memory_mb) {
      const usedPct = ((router.total_memory_mb - router.free_memory_mb) / router.total_memory_mb) * 100;
      setMemHistory(prev => pushHistory(prev, usedPct));
    }
    if (router.temperature != null) {
      setTempHistory(prev => pushHistory(prev, router.temperature));
    }
  }, [router]);

  // Which external service the public IP links to. Configured in Settings;
  // a failure here simply leaves the address as plain text.
  const [lookupService, setLookupService] = useState(null);

  useEffect(() => {
    api.getIpLookup()
      .then(res => {
        const data = res?.data;
        if (!data?.services) return;
        setLookupService(data.services.find(s => s.id === data.default_id) || null);
      })
      .catch(() => {});
  }, []);

  // Warning threshold is configured in Settings; used to colour the temperature.
  useEffect(() => {
    api.getSettings()
      .then(res => {
        const raw = res?.data?.temp_warning_threshold;
        if (raw) setTempThreshold(Number(raw));
      })
      .catch(() => {});
  }, []);

  // Load configured interfaces
  useEffect(() => {
    if (activeRouter?.id) {
      api.getMonitoredInterfacesConfig(activeRouter.id)
        .then(res => {
          if (res?.data?.selected_interfaces) {
            setSelectedIfaces(res.data.selected_interfaces);
          }
          if (res?.data?.ignored_discovery_interfaces) {
            setIgnoredDiscoveryIfaces(res.data.ignored_discovery_interfaces);
          }
        })
        .catch(() => {});
    }
  }, [activeRouter?.id]);

  const openConfigModal = async () => {
    setModalOpen(true);
    setSaveSuccess(false);
    setModalTab('wan');
    try {
      const [ifacesRes, cfgRes] = await Promise.all([
        api.getAvailableInterfaces(activeRouter?.id).catch(() => ({ data: [] })),
        api.getMonitoredInterfacesConfig(activeRouter?.id).catch(() => ({ data: { selected_interfaces: [], ignored_discovery_interfaces: [] } }))
      ]);
      const list = ifacesRes.data || interfaces || [];
      setAvailableIfaces(list);
      setSelectedIfaces(cfgRes.data?.selected_interfaces || []);
      setIgnoredDiscoveryIfaces(cfgRes.data?.ignored_discovery_interfaces || []);
    } catch (err) {
      console.error('Failed to load interfaces config', err);
    }
  };

  const handleToggleIface = (name) => {
    setSelectedIfaces(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]
    );
  };

  const handleToggleIgnoredIface = (name) => {
    setIgnoredDiscoveryIfaces(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]
    );
  };

  // Flatten the interface list into render order: each top-level interface
  // followed by the VLANs / PPPoE clients / bridge ports that ride on it, with
  // a depth for indentation. Anything whose parent is not itself in the list is
  // treated as top-level.
  const nestedIfaces = React.useMemo(() => {
    const names = new Set(availableIfaces.map(i => i.name));
    const childrenOf = (parent) =>
      availableIfaces
        .filter(i => i.parent === parent)
        .sort((a, b) => a.name.localeCompare(b.name));
    const out = [];
    const walk = (iface, depth) => {
      out.push({ iface, depth });
      childrenOf(iface.name).forEach(c => walk(c, depth + 1));
    };
    availableIfaces
      .filter(i => !i.parent || !names.has(i.parent))
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach(root => walk(root, 0));
    // Safety net for a parent cycle - never drop an interface from the list.
    const shown = new Set(out.map(o => o.iface.name));
    availableIfaces.forEach(i => { if (!shown.has(i.name)) out.push({ iface: i, depth: 0 }); });
    return out;
  }, [availableIfaces]);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await api.saveMonitoredInterfacesConfig(activeRouter?.id, selectedIfaces, ignoredDiscoveryIfaces);
      setSaveSuccess(true);
      setTimeout(() => {
        setModalOpen(false);
        setSaveSuccess(false);
      }, 500);
    } catch (err) {
      console.error('Failed to save monitored interfaces', err);
    } finally {
      setIsSaving(false);
    }
  };

  if (!router) {
    return null;
  }

  const cpuLoad = router.cpu_load || 0;
  const cpuColor = cpuLoad > 85 ? 'var(--color-danger)' : (cpuLoad > 60 ? 'var(--color-warning)' : 'var(--color-success)');

  const usedMemMb = router.total_memory_mb
    ? Math.round(router.total_memory_mb - (router.free_memory_mb || 0))
    : null;
  const totalMemMb = router.total_memory_mb ? Math.round(router.total_memory_mb) : null;
  const memPct = router.total_memory_mb
    ? Math.round(((router.total_memory_mb - router.free_memory_mb) / router.total_memory_mb) * 100)
    : null;

  // Temperature is judged against the user's configured warning threshold
  // rather than a hard-coded number, so it matches the alerting behaviour.
  const temp = router.temperature;
  const warnAt = tempThreshold || 80;
  const tempColor = temp == null
    ? 'var(--text-muted)'
    : (temp >= warnAt ? 'var(--color-danger)'
      : (temp >= warnAt - 8 ? 'var(--color-warning)' : 'var(--color-success)'));
  // A tile only becomes clickable when someone is listening, so it never
  // advertises an affordance that does nothing.
  const goHealth = onNavigate ? () => onNavigate('health') : undefined;
  const goUsers = onNavigate ? () => onNavigate('users') : undefined;

  // The client tile leads with the number of profiles and carries their device
  // count underneath (online / total). Older telemetry frames only had
  // `active_clients`, so fall back to it rather than showing a bare zero.
  const userCount = router.user_count ?? 0;
  const onlineDevices = router.active_clients ?? 0;
  const clientDevices = router.client_device_count ?? onlineDevices;

  // CPU tile subtitle: the processor model, then its characteristics. The model
  // line is dropped when it is only the architecture repeated (a CHR or x86
  // box with no SoC name), leaving just the spec line.
  //
  // `cpu_model_exact` false means RouterOS could only name the SoC *family*
  // (its bootloader platform, e.g. "ipq5300") because this product code is not
  // in the published-specification table. Mark it, so a family is never read as
  // a part number.
  const cpuExact = router.cpu_model_exact !== false;
  const cpuModel = router.cpu_model || '';
  const cpuArch = router.cpu_arch || '';
  // The architecture moves into the tile's heading - "CPU (arm64)" - where it
  // costs no vertical space at all. It is dropped when it only repeats the
  // model, which is what a CHR or an x86 box reports.
  const cpuArchLabel = cpuArch && cpuArch.toLowerCase() !== cpuModel.toLowerCase() ? cpuArch : null;
  const cpuLabel = cpuArchLabel ? `${t('cpu')} (${cpuArchLabel})` : t('cpu');
  // Model, core count and clock now share a single line - "IPQ-5322 · 4C ·
  // 1.1 GHz". The abbreviations are what buy that: "4C" and gigahertz leave
  // room for the model name, which is the one part of the line a reader cannot
  // reconstruct from anything else on screen.
  const cpuSpecLine = [
    cpuModel || null,
    router.cpu_count ? `${router.cpu_count}C` : null,
    formatFrequency(router.cpu_frequency_mhz, t('ghz')),
  ].filter(Boolean).join(' · ');
  const cpuLines = cpuSpecLine ? [<Footnote key="cpu-spec" text={cpuSpecLine} />] : [];
  // Say outright which of the two the model line is, so nobody has to guess
  // whether "ipq5300" is this board's part number (it is not - it is the family
  // its bootloader belongs to).
  const cpuTitle = [
    cpuModel && (cpuExact ? t('cpu_model_exact') : t('cpu_model_family')),
    !cpuExact && router.cpu_platform ? `${t('cpu_platform')}: ${router.cpu_platform}` : null,
    onNavigate ? t('open_health_hint') : null,
  ].filter(Boolean).join('\n') || undefined;

  // Public address and operator, folded into one sub-line. The address keeps
  // its link to the configured lookup service; the operator follows it after a
  // separator. Together they routinely overrun the tile, so the pair is wrapped
  // in a Footnote: clipped text gets the dotted underline and the full string
  // on hover, exactly as the processor name does one tile over.
  const showPublicIp = !!router.public_ip && router.public_ip !== router.wan_ip;
  const publicNetText = [showPublicIp ? router.public_ip : null, router.isp || null]
    .filter(Boolean)
    .join(' · ');
  const publicNetLine = publicNetText
    ? (
      <Footnote key="net" text={publicNetText}>
        {showPublicIp ? <PublicIpLink ip={router.public_ip} service={lookupService} t={t} /> : null}
        {showPublicIp && router.isp ? ' · ' : ''}
        {router.isp || ''}
      </Footnote>
    )
    : null;

  // The WAN set is exactly what the admin ticked in the selector - never
  // inferred from the routing table. `monitored_interfaces` on the telemetry
  // frame is that saved selection; `selectedIfaces` is the same list read
  // straight from config before the first frame arrives.
  const monitored = (router.monitored_interfaces && router.monitored_interfaces.length)
    ? router.monitored_interfaces
    : (selectedIfaces || []);
  const hasWan = monitored.length > 0;
  const monitoredShort = !hasWan
    ? t('ifaces_none')
    : monitored.length <= 2
      ? monitored.join(', ')
      : `${monitored.slice(0, 2).join(', ')} +${monitored.length - 2}`;
  // The two bandwidth tiles are measured across exactly these interfaces. With
  // nothing selected there is no WAN to measure, so the tiles say so loudly
  // and turn amber rather than quietly reading 0 bps.
  const wanSub = hasWan
    ? `${t('wan_label')} · ${monitoredShort}`
    : <span style={{ color: 'var(--color-warning)', fontWeight: 700 }}>⚠ {t('wan_none_warning')}</span>;
  const wanTone = hasWan ? null : 'var(--color-warning)';
  const wanTileTitle = hasWan
    ? `${t('configure_interfaces_hint')}\n${t('wan_label')}: ${monitored.join(', ')}`
    : `${t('wan_none_warning')}\n${t('configure_interfaces_hint')}`;

  return (
    <>
      <div style={{
        display: 'grid',
        // A quarter narrower than the original 148px floor, now that the
        // value moved onto the label's row instead of needing a line of its own.
        gridTemplateColumns: 'repeat(auto-fit, minmax(111px, 1fr))',
        gap: 10,
        marginBottom: 18,
        alignItems: 'stretch'
      }}>
        {/* No label: the arrow icon already says download vs upload, and
            "Download"/"Upload" were two of the four strings that no longer
            fit once the value moved onto the same row as the label. */}
        <Tile
          icon={<ArrowDown size={15} />}
          tone={wanTone || 'var(--color-success)'}
          value={formatSpeed(router.wan_rx_bps, speedUnit)}
          sub={wanSub}
          history={rxHistory}
          onClick={openConfigModal}
          title={wanTileTitle}
        />

        <Tile
          icon={<ArrowUp size={15} />}
          tone={wanTone || 'var(--color-primary)'}
          value={formatSpeed(router.wan_tx_bps, speedUnit)}
          sub={wanSub}
          history={txHistory}
          onClick={openConfigModal}
          title={wanTileTitle}
        />

        <Tile
          icon={<Cpu size={15} />}
          tone={cpuColor}
          label={cpuLabel}
          value={`${cpuLoad}%`}
          // The actual processor, not the board. On MikroTik hardware the SoC
          // name ("ipq5300") comes from /system/routerboard; the arch, core
          // count and clock come from /system/resource. The board name still
          // sits on the Uptime tile.
          sub={cpuLines}
          history={cpuHistory}
          historyMax={100}
          onClick={goHealth}
          title={cpuTitle}
        />

        <Tile
          icon={<HardDrive size={15} />}
          tone="var(--color-primary)"
          // "RAM Free" (and its Russian equivalent, wider still) doesn't fit
          // beside the value at this width; the sub-line's "N% used" already
          // frames the headline figure as the free side of that split.
          label={t('tile_ram_label')}
          value={`${Math.round(router.free_memory_mb || 0)} MB`}
          sub={memPct !== null ? `${memPct}% ${t('used_label')} · ${usedMemMb}/${totalMemMb} MB` : ''}
          history={memHistory}
          historyMax={100}
          onClick={goHealth}
          title={onNavigate ? t('open_health_hint') : undefined}
        />

        <Tile
          icon={<Thermometer size={15} />}
          tone={tempColor}
          label={t('temp')}
          value={router.temperature != null ? `${router.temperature}°C` : '—'}
          sub={tempThreshold ? `${t('threshold_label')} ${tempThreshold}°C` : ''}
          history={tempHistory}
          onClick={goHealth}
          title={onNavigate ? t('open_health_hint') : undefined}
        />

        <Tile
          icon={<Users size={15} />}
          tone="var(--color-primary)"
          // A dedicated (shorter) label rather than the shared `clients_label`
          // used on the Users tab header, where the full word has room.
          label={t('tile_users_label')}
          value={String(userCount)}
          sub={t('clients_devices_sub', { online: onlineDevices, total: clientDevices })}
          onClick={goUsers}
          title={onNavigate ? t('open_users_hint') : undefined}
        />

        <Tile
          icon={<Globe size={15} />}
          tone="var(--text-secondary)"
          label="IP"
          value={router.wan_ip || '—'}
          valueSize="var(--fs-sm)"
          title={router.isp
            ? `${t('isp_label')}: ${router.isp}${router.asn ? ` (${router.asn})` : ''}`
            : undefined}
          // The address the internet actually sees (it differs from the one on
          // the interface under carrier-grade NAT) and the operator it belongs
          // to are one fact about one link, so they share one line. They used to
          // take a line each, which made this the tallest tile in the bar and
          // therefore set the height of every other tile beside it.
          sub={[
            publicNetLine,
            // The line's measured speed belongs with its address and its owner.
            activeRouterId
              ? <SpeedTestBadge key="speedtest" routerId={activeRouterId} onNavigate={onNavigate} />
              : null,
            !router.public_ip && !router.isp ? (router.version || '') : null
          ]}
        />
      </div>

      {/* Interface Configuration Modal */}
      {modalOpen && (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <div className="modal-card modal-sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div className="panel-title">
                <Network size={18} />
                {t('gateway_ifaces_title')}
              </div>
              <button className="btn-icon" onClick={() => setModalOpen(false)} style={{ width: 28, height: 28 }}>
                <X size={16} />
              </button>
            </div>

            <div style={{ display: 'flex', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)', padding: '0 16px' }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                style={{
                  borderRadius: 0,
                  borderBottom: modalTab === 'wan' ? '2px solid var(--color-primary)' : '2px solid transparent',
                  color: modalTab === 'wan' ? 'var(--color-primary)' : 'var(--text-secondary)',
                  fontWeight: modalTab === 'wan' ? 700 : 500,
                  padding: '8px 12px'
                }}
                onClick={() => setModalTab('wan')}
              >
                {t('tab_wan_ifaces')} ({selectedIfaces.length})
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                style={{
                  borderRadius: 0,
                  borderBottom: modalTab === 'discovery' ? '2px solid var(--color-primary)' : '2px solid transparent',
                  color: modalTab === 'discovery' ? 'var(--color-primary)' : 'var(--text-secondary)',
                  fontWeight: modalTab === 'discovery' ? 700 : 500,
                  padding: '8px 12px'
                }}
                onClick={() => setModalTab('discovery')}
              >
                {t('tab_discovery_ifaces')} ({ignoredDiscoveryIfaces.length})
              </button>
            </div>

            <div className="modal-body">
              {modalTab === 'wan' ? (
                <>
                  <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 14, lineHeight: 1.4 }}>
                    {t('gateway_ifaces_desc')}
                  </p>

                  {/* Interface list. Tick the interface(s) that face the internet;
                      a VLAN or PPPoE link is shown nested under the port it runs
                      on. The selected set is what the WAN counters sum over. */}
                  <div className="list-box" style={{ maxHeight: 280 }}>
                    {availableIfaces.length === 0 ? (
                      <div className="empty-note">{t('loading_interfaces')}</div>
                    ) : (
                      nestedIfaces.map(({ iface, depth }) => {
                        const isChecked = selectedIfaces.includes(iface.name);
                        return (
                          <div
                            key={iface.name}
                            className={`list-row${isChecked ? ' is-selected' : ''}`}
                            onClick={() => handleToggleIface(iface.name)}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, paddingLeft: depth * 20 }}>
                              {depth > 0 && (
                                <span style={{ color: 'var(--text-muted)', flexShrink: 0, marginLeft: -14 }}>↳</span>
                              )}
                              <input
                                type="checkbox"
                                checked={isChecked}
                                readOnly
                                style={{ cursor: 'pointer', pointerEvents: 'none' }}
                              />
                              <span style={{
                                width: 8,
                                height: 8,
                                borderRadius: 'var(--radius-full)',
                                background: iface.running ? 'var(--color-success)' : 'var(--text-muted)',
                                flexShrink: 0
                              }} />
                              <span className="truncate" style={{ fontWeight: isChecked ? 700 : 500 }}>{iface.name}</span>
                              {/* The WAN badge marks the admin's choice, nothing
                                  else: it appears only on ticked rows. */}
                              {isChecked && (
                                <span
                                  className="badge badge-primary"
                                  style={{ fontSize: 'var(--fs-3xs)', padding: '1px 5px', flexShrink: 0 }}
                                  title={t('wan_iface_hint')}
                                >
                                  {t('wan_label')}
                                </span>
                              )}
                            </div>
                            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                              {/* Routing-table steer, kept as a faint hint so it can
                                  never be mistaken for the selection itself. */}
                              {iface.is_wan && !isChecked && (
                                <span style={{ opacity: 0.7, fontStyle: 'italic' }}>{t('wan_detected_hint')}</span>
                              )}
                              <span>{iface.parent ? t('iface_on_parent', { parent: iface.parent }) : (iface.type || 'interface')}</span>
                            </span>
                          </div>
                        );
                      })
                    )}
                  </div>
                </>
              ) : (
                <>
                  <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 14, lineHeight: 1.4 }}>
                    {t('discovery_ifaces_desc')}
                  </p>

                  <div className="list-box" style={{ maxHeight: 280 }}>
                    {availableIfaces.length === 0 ? (
                      <div className="empty-note">{t('loading_interfaces')}</div>
                    ) : (
                      nestedIfaces.map(({ iface, depth }) => {
                        const isChecked = ignoredDiscoveryIfaces.includes(iface.name);
                        return (
                          <div
                            key={iface.name}
                            className={`list-row${isChecked ? ' is-selected' : ''}`}
                            onClick={() => handleToggleIgnoredIface(iface.name)}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, paddingLeft: depth * 20 }}>
                              {depth > 0 && (
                                <span style={{ color: 'var(--text-muted)', flexShrink: 0, marginLeft: -14 }}>↳</span>
                              )}
                              <input
                                type="checkbox"
                                checked={isChecked}
                                readOnly
                                style={{ cursor: 'pointer', pointerEvents: 'none' }}
                              />
                              <span style={{
                                width: 8,
                                height: 8,
                                borderRadius: 'var(--radius-full)',
                                background: iface.running ? 'var(--color-success)' : 'var(--text-muted)',
                                flexShrink: 0
                              }} />
                              <span className="truncate" style={{ fontWeight: isChecked ? 700 : 500 }}>{iface.name}</span>
                              {isChecked && (
                                <span
                                  className="badge badge-warning"
                                  style={{ fontSize: 'var(--fs-3xs)', padding: '1px 5px', flexShrink: 0 }}
                                >
                                  {t('discovery_excluded_badge')}
                                </span>
                              )}
                            </div>
                            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                              {iface.is_tunnel && (
                                <span style={{ opacity: 0.8, fontStyle: 'italic' }}>{t('auto_excluded_hint')}</span>
                              )}
                              <span>{iface.parent ? t('iface_on_parent', { parent: iface.parent }) : (iface.type || 'interface')}</span>
                            </span>
                          </div>
                        );
                      })
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="modal-footer">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setModalOpen(false)}>
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSave}
                disabled={isSaving}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {saveSuccess ? <Check size={14} /> : null}
                {saveSuccess ? t('saved_ifaces_success') : t('save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
