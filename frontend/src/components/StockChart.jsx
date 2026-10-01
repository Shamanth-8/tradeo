import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType } from 'lightweight-charts'

function StockChart({ data, symbol, height = 400 }) {
    const chartContainerRef = useRef()
    const [chartType, setChartType] = useState('candlestick')

    useEffect(() => {
        if (!chartContainerRef.current || !data?.candles?.length) return

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: '#1e293b' },
                textColor: '#94a3b8',
            },
            grid: {
                vertLines: { color: '#334155' },
                horzLines: { color: '#334155' },
            },
            width: chartContainerRef.current.clientWidth,
            height: height,
            crosshair: {
                mode: 1,
            },
            timeScale: {
                borderColor: '#334155',
                timeVisible: true,
            },
            rightPriceScale: {
                borderColor: '#334155',
            },
        })

        // Candlestick series
        const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderUpColor: '#22c55e',
            borderDownColor: '#ef4444',
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
        })

        // Format data for the chart
        const formattedData = data.candles.map(candle => ({
            time: candle.time,
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
        }))

        candlestickSeries.setData(formattedData)

        // Volume series
        if (data.volume?.length) {
            const volumeSeries = chart.addHistogramSeries({
                color: '#0ea5e9',
                priceFormat: {
                    type: 'volume',
                },
                priceScaleId: '',
                scaleMargins: {
                    top: 0.8,
                    bottom: 0,
                },
            })

            const volumeData = data.volume.map((v, i) => ({
                time: v.time,
                value: v.value,
                color: formattedData[i]?.close >= formattedData[i]?.open ? '#22c55e40' : '#ef444440',
            }))

            volumeSeries.setData(volumeData)
        }

        // Fit content
        chart.timeScale().fitContent()

        // Handle resize
        const handleResize = () => {
            chart.applyOptions({ width: chartContainerRef.current.clientWidth })
        }

        window.addEventListener('resize', handleResize)

        return () => {
            window.removeEventListener('resize', handleResize)
            chart.remove()
        }
    }, [data, height])

    return (
        <div className="glass-card p-4">
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-white">{symbol} Price Chart</h3>
                <div className="flex items-center gap-2">
                    {['1D', '1W', '1M', '3M', '6M', '1Y'].map((period) => (
                        <button
                            key={period}
                            className="px-3 py-1 text-sm font-medium rounded bg-dark-700 text-dark-400 hover:text-white hover:bg-dark-600 transition-colors"
                        >
                            {period}
                        </button>
                    ))}
                </div>
            </div>
            <div ref={chartContainerRef} className="rounded-lg overflow-hidden" />
        </div>
    )
}

export default StockChart
