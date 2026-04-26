import React from 'react';
import { Box } from 'ink';
import { FixedWindowScroller } from './FixedWindowScroller';
import { usePerformanceMonitor } from '../hooks/usePerformance';

/**
 * OptimizedTranscriptPane is a drop-in replacement for the transcript area
 * that uses virtualization to dramatically improve performance with large
 * message histories.
 */
export const OptimizedTranscriptPane = React.memo(({
  messages,
  renderMessage,
  height,
  width,
  onScroll,
}) => {
  const { logEvent } = usePerformanceMonitor('OptimizedTranscriptPane', { 
    logToConsole: false 
  });
  
  // Reference to the scroller component
  const scrollerRef = React.useRef(null);
  
  // Keep track of visible window for debugging
  const [visibleRange, setVisibleRange] = React.useState({ start: 0, end: 0 });
  
  // Handle scroll events 
  const handleScroll = React.useCallback((scrollInfo) => {
    setVisibleRange({
      start: scrollInfo.firstVisibleItemIndex,
      end: scrollInfo.lastVisibleItemIndex
    });
    
    if (onScroll) {
      onScroll(scrollInfo);
    }
  }, [onScroll]);

  // Memoize the render function for better performance
  const renderItem = React.useCallback(({ item, index, isVisible }) => {
    if (!isVisible) {
      return <Box height={3} />; // Placeholder with approximate height
    }
    
    return renderMessage(item, index);
  }, [renderMessage]);
  
  // Log performance data
  React.useEffect(() => {
    logEvent(`render-range-${visibleRange.start}-${visibleRange.end}`);
  }, [visibleRange, logEvent]);
  
  return (
    <Box 
      flexDirection="column"
      height={height}
      width={width}
      style={{ scrollbarGutter: 'stable' }}
    >
      <FixedWindowScroller
        ref={scrollerRef}
        items={messages}
        height={height}
        width={width}
        itemHeight={3} // Average message height (will be refined)
        renderItem={renderItem}
        overscrollItems={25} // Number of off-screen items to keep mounted
        onScroll={handleScroll}
        initialScrollToEnd={true}
      />
    </Box>
  );
});

export default OptimizedTranscriptPane;