import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { RouterConnectionForm } from './RouterConnectionForm';
import { api } from '../api/client';

/**
 * Editing a router's connection details was impossible until this form existed:
 * the API supported it all along, but nothing in the UI called it, so a router
 * whose stored settings stopped working could only be deleted — taking its
 * traffic rollups and hardware metrics with it, since both cascade on the
 * router row.
 *
 * The subtle part is the password. It is never returned by the API, so an edit
 * form cannot pre-fill it and a blank box has to mean "keep the saved one".
 * These tests pin that, and pin the refusal to test-connect with a blank
 * password — sending one registers on the router as a failed login for the
 * named user, which is what previously filled the log with what looked like a
 * brute-force attempt.
 */

vi.mock('../api/client', () => ({
  api: {
    testRouterConnection: vi.fn(),
    autoProvisionSslDirect: vi.fn(),
  },
}));

const existing = {
  id: 1,
  name: 'Main Router',
  host: '192.168.88.1',
  port: 443,
  use_ssl: true,
  ssl_verify: false,
  username: 'rest',
};

beforeEach(() => vi.clearAllMocks());

const renderEdit = (onSubmit = vi.fn()) => {
  renderWithProviders(
    <RouterConnectionForm mode="edit" initial={existing} onSubmit={onSubmit} onCancel={() => {}} />
  );
  return onSubmit;
};

describe('editing an existing router', () => {
  it('pre-fills the details that can be read back', () => {
    renderEdit();
    expect(screen.getByDisplayValue('Main Router')).toBeInTheDocument();
    expect(screen.getByDisplayValue('192.168.88.1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('rest')).toBeInTheDocument();
  });

  it('leaves the password blank, because the API never returns it', () => {
    renderEdit();
    const password = document.querySelector('input[type="password"]');
    expect(password.value).toBe('');
  });

  it('omits the password entirely when it was left blank', async () => {
    // Sending an empty string would overwrite the stored password with nothing.
    const onSubmit = renderEdit();
    fireEvent.change(screen.getByDisplayValue('192.168.88.1'), { target: { value: '192.168.88.2' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.host).toBe('192.168.88.2');
    expect('password' in payload).toBe(false);
  });

  it('sends the password when a new one was typed', async () => {
    const onSubmit = renderEdit();
    const password = document.querySelector('input[type="password"]');
    fireEvent.change(password, { target: { value: 'new-secret' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0].password).toBe('new-secret');
  });
});

describe('testing the connection', () => {
  it('refuses to fire with a blank password', () => {
    // The saved password cannot be read back, so testing as-is would send an
    // empty one and the router would log a failed login for that user.
    renderEdit();
    expect(screen.getByRole('button', { name: /test/i })).toBeDisabled();
  });

  it('becomes available once a password is supplied', () => {
    renderEdit();
    fireEvent.change(document.querySelector('input[type="password"]'), {
      target: { value: 'secret' },
    });
    expect(screen.getByRole('button', { name: /test/i })).toBeEnabled();
  });

  it('reports what it connected to', async () => {
    api.testRouterConnection.mockResolvedValue({
      data: { success: true, board_name: 'hAP be3', ros_version: '7.25' },
    });
    renderEdit();
    fireEvent.change(document.querySelector('input[type="password"]'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /test/i }));

    expect(await screen.findByText(/hAP be3/)).toBeInTheDocument();
  });

  it('offers a failed probe\'s own suggestion as a one-click correction', async () => {
    // Recovering a factory-reset router is exactly this case: HTTPS is gone and
    // the router answers on plain HTTP.
    api.testRouterConnection.mockResolvedValue({
      data: {
        success: false,
        message: 'HTTPS is not enabled on router, but HTTP (port 80) connected',
        suggested_port: 80,
        suggested_ssl: false,
      },
    });
    renderEdit();
    fireEvent.change(document.querySelector('input[type="password"]'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /test/i }));

    const apply = await screen.findByRole('button', { name: /HTTP :80/ });
    fireEvent.click(apply);

    expect(screen.getByDisplayValue('80')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'HTTP' })).toBeInTheDocument();
  });

  it('drops a stale verdict when the settings it judged are changed', async () => {
    // Leaving "Connected!" on screen would vouch for settings that are no
    // longer the ones displayed.
    api.testRouterConnection.mockResolvedValue({
      data: { success: true, board_name: 'hAP be3', ros_version: '7.25' },
    });
    renderEdit();
    fireEvent.change(document.querySelector('input[type="password"]'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /test/i }));
    expect(await screen.findByText(/hAP be3/)).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue('192.168.88.1'), {
      target: { value: '10.0.0.1' },
    });

    expect(screen.queryByText(/hAP be3/)).toBeNull();
  });
});

describe('transport', () => {
  it('switches port with the scheme, since the two always move together', () => {
    renderEdit();
    fireEvent.click(screen.getByRole('button', { name: 'HTTPS' }));

    expect(screen.getByDisplayValue('80')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'HTTP' })).toBeInTheDocument();
  });
});

describe('adding a router', () => {
  it('does not pre-fill a username nobody chose', () => {
    // Pre-filling "admin" meant one click on Test Connection probed the router
    // with a username the operator never picked, and the probe chain turned it
    // into several failed logins in the router's log.
    renderWithProviders(
      <RouterConnectionForm mode="create" onSubmit={() => {}} onCancel={() => {}} />
    );
    const inputs = [...document.querySelectorAll('input[type="text"]')];
    const usernameField = inputs[inputs.length - 1];
    expect(usernameField.value).toBe('');
  });

  it('offers 1-click Auto-SSL provisioning when connected over HTTP', async () => {
    api.testRouterConnection.mockResolvedValue({
      data: { success: true, board_name: 'CCR1009', ros_version: '7.24.1' }
    });
    api.autoProvisionSslDirect.mockResolvedValue({
      data: { success: true, port: 443, message: 'SSL provisioned' }
    });

    renderWithProviders(
      <RouterConnectionForm
        mode="create"
        initial={{ name: 'Test', host: '10.0.0.1', port: 80, use_ssl: false, username: 'rest' }}
        onSubmit={() => {}}
        onCancel={() => {}}
      />
    );

    // Type password so test is enabled
    fireEvent.change(screen.getByPlaceholderText(/password/i), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByText(/Auto-Configure SSL/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Auto-Configure SSL/i));

    await waitFor(() => {
      expect(api.autoProvisionSslDirect).toHaveBeenCalled();
    });
  });
});
