import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import GettingStarted from '../pages/GettingStarted';

describe('Staff photo quick reference', () => {
  it('explains existing photo sources and opens the uploader in one click', () => {
    const onOpenPhotos = vi.fn();

    render(
      <GettingStarted
        onOpenDocuments={vi.fn()}
        onOpenCRM={vi.fn()}
        onOpenAdStudio={vi.fn()}
        onOpenPhotos={onOpenPhotos}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Adding Photos/i }));

    expect(screen.getByText(/actual on-lot photos/i)).toBeInTheDocument();
    expect(screen.getByText(/orderable floorplans/i)).toBeInTheDocument();
    expect(screen.getByText(/show only homes that still need photos/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Open Photos/i }));
    expect(onOpenPhotos).toHaveBeenCalledTimes(1);
  });
});
