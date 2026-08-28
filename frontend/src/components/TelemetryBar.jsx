import React from 'react';
import { useI18n } from '../context/I18nContext';
import { formatSpeed, formatBytes, formatUptime } from '../utils/formatters';
import { Cpu, HardDrive, Thermometer, Zap, ArrowDown, ArrowUp, Clock } from 'lucide-react';

export function TelemetryBar({ router }) {
  const { t } = useI18n();

  if (!router) {
    return null;
  }

  const cpuLoad = router.cpu_load || 0;
  const cpuColor = cpuLoad > 85 ? 'var(--color-danger)' : (cpuLoad > 60 ? 'var(--color-warning)' : 'var(--color-success)');

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
      gap: 14,
      marginBottom: 24
    }}>
      {/* Gateway Download */}
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{
          background: 'rgba(46, 204, 113, 0.15)',
          color: 'var(--color-success)',
          padding: 10,
          borderRadius: 'var(--radius-md)'
        }}>
          <ArrowDown size={20} />
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_rx')}</div>
          <div className="font-mono" style={{ fontSize: '1.15rem', fontWeight: 800, color: 'var(--color-success)' }}>
            {formatSpeed(router.wan_rx_bps)}
          </div>
        </div>
      </div>

      {/* Gateway Upload */}
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{
          background: 'rgba(11, 114, 201, 0.15)',
          color: 'var(--color-primary)',
          padding: 10,
          borderRadius: 'var(--radius-md)'
        }}>
          <ArrowUp size={20} />
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_tx')}</div>
          <div className="font-mono" style={{ fontSize: '1.15rem', fontWeight: 800, color: 'var(--color-primary)' }}>
            {formatSpeed(router.wan_tx_bps)}
          </div>
        </div>
      </div>

      {/* CPU Load */}
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{
          background: 'var(--bg-secondary)',
          color: cpuColor,
          padding: 10,
          borderRadius: 'var(--radius-md)'
        }}>
          <Cpu size={20} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('cpu')}</span>
            <span className="font-mono" style={{ fontSize: '0.85rem', fontWeight: 700, color: cpuColor }}>{cpuLoad}%</span>
          </div>
          <div style={{
            height: 6,
            background: 'var(--bg-input)',
            borderRadius: 3,
            marginTop: 6,
            overflow: 'hidden'
          }}>
            <div style={{
              width: `${Math.min(cpuLoad, 100)}%`,
              height: '100%',
              background: cpuColor,
              transition: 'width 0.4s ease'
            }}></div>
          </div>
        </div>
      </div>

      {/* Memory */}
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{
          background: 'var(--bg-secondary)',
          color: 'var(--color-info)',
          padding: 10,
          borderRadius: 'var(--radius-md)'
        }}>
          <HardDrive size={20} />
        </div>
        <div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('ram')} Free</div>
          <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>
            {router.free_memory_mb || 0} <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>MB</span>
          </div>
        </div>
      </div>

      {/* Temp / Voltage */}
      {router.temperature !== null && (
        <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'var(--bg-secondary)',
            color: (router.temperature > 65 ? 'var(--color-danger)' : 'var(--color-warning)'),
            padding: 10,
            borderRadius: 'var(--radius-md)'
          }}>
            <Thermometer size={20} />
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('temp')}</div>
            <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>
              {router.temperature}°C
            </div>
          </div>
        </div>
      )}

      {/* Uptime */}
      {router.uptime && (
        <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'var(--bg-secondary)',
            color: 'var(--text-secondary)',
            padding: 10,
            borderRadius: 'var(--radius-md)'
          }}>
            <Clock size={20} />
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('uptime')}</div>
            <div className="font-mono" style={{ fontSize: '0.925rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
              {router.uptime}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
