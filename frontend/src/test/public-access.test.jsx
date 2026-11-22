import { screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import AppLayout from '../layouts/AppLayout.jsx';
import AccountPage from '../pages/AccountPage.jsx';
import { HEALTH, mockApi, renderPage } from './helpers.jsx';

const routes = {
  'GET /api/health': { ...HEALTH, public_access: true, auth_required: false },
  'GET /api/auth/me': { user: { id: 'public-workspace', name: 'Public workspace reviewer', email: '', role: 'admin', workspace: 'default', public_access: true } },
};

it('opens the workspace without sign-in or sign-out controls', async () => {
  mockApi(routes);
  renderPage(<AppLayout />);
  expect(await screen.findByRole('link', { name: 'System status' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument();
});

it('shows system status without account setup or passwords', async () => {
  mockApi(routes);
  renderPage(<AccountPage />);
  expect(await screen.findByRole('heading', { name: 'System status' })).toBeInTheDocument();
  expect(screen.queryByText('Add an account')).not.toBeInTheDocument();
  expect(screen.queryByText('Change your password')).not.toBeInTheDocument();
});
