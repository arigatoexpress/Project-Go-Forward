import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { SkeletonCard, SkeletonList } from '../components/Skeleton';

describe('Skeleton components', () => {
  it('SkeletonCard renders without crashing', () => {
    const { container } = render(<SkeletonCard />);
    // Top-level skeleton card uses the animate-pulse utility class.
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('SkeletonList renders N SkeletonCards', () => {
    const { container } = render(<SkeletonList count={4} />);
    // Each SkeletonCard is the only direct child of the list wrapper.
    const wrapper = container.firstChild;
    expect(wrapper).not.toBeNull();
    expect(wrapper.children).toHaveLength(4);
  });

  it('SkeletonList defaults to 5 items when count is omitted', () => {
    const { container } = render(<SkeletonList />);
    const wrapper = container.firstChild;
    expect(wrapper.children).toHaveLength(5);
  });
});
