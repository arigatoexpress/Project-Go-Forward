import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AddPasskeyDialog from '../components/AddPasskeyDialog';

describe('Add passkey dialog', () => {
  it('tells an email session the passkey is saved for that sign-in', () => {
    render(
      <AddPasskeyDialog
        open
        phase="confirm"
        email="mark@texashomeoutlet.com"
        onAdd={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText(/email you are signed in with/i)).toBeInTheDocument();
    expect(screen.queryByText(/backup PIN/i)).not.toBeInTheDocument();
  });

  it('lets a backup PIN choose an owner address without asking for an old passkey', () => {
    const onAdd = vi.fn();
    render(
      <AddPasskeyDialog
        open
        phase="confirm"
        recovery
        email="aribspector@gmail.com"
        emailChoices={['aribspector@gmail.com', 'aristotlespec@gmail.com']}
        onAdd={onAdd}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText(/backup PIN can add it/i)).toBeInTheDocument();
    expect(screen.queryByText(/existing passkey/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Owner email'), {
      target: { value: 'aristotlespec@gmail.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add a passkey' }));
    expect(onAdd).toHaveBeenCalledWith('aristotlespec@gmail.com');
  });
});
