import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Clock,
  Loader2,
  Search,
  Square,
  TrainFront,
} from "lucide-react";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import {
  ApiError,
  ProviderStatusResponse,
  RouteSearchResponse,
  getProviderStatus,
  searchRoutesStream,
  searchStations,
} from "./lib/api";

const today = new Date().toISOString().slice(0, 10);

export function App() {
  const [fromStation, setFromStation] = useState("北京");
  const [toStation, setToStation] = useState("上海");
  const [date, setDate] = useState(today);
  const [minTransferMinutes, setMinTransferMinutes] = useState(30);
  const [fromStationOptions, setFromStationOptions] = useState<string[]>([]);
  const [toStationOptions, setToStationOptions] = useState<string[]>([]);
  const [result, setResult] = useState<RouteSearchResponse | null>(null);
  const [providerStatus, setProviderStatus] =
    useState<ProviderStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let ignore = false;

    async function loadProviderStatus() {
      try {
        const status = await getProviderStatus();
        if (!ignore) {
          setProviderStatus(status);
        }
      } catch {
        if (!ignore) {
          setProviderStatus(null);
        }
      }
    }

    loadProviderStatus();
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    let ignore = false;

    async function loadStations() {
      try {
        const stations = await searchStations(fromStation);
        if (!ignore) {
          setFromStationOptions(stations);
        }
      } catch {
        if (!ignore) {
          setFromStationOptions([]);
        }
      }
    }

    loadStations();
    return () => {
      ignore = true;
    };
  }, [fromStation]);

  useEffect(() => {
    let ignore = false;

    async function loadStations() {
      try {
        const stations = await searchStations(toStation);
        if (!ignore) {
          setToStationOptions(stations);
        }
      } catch {
        if (!ignore) {
          setToStationOptions([]);
        }
      }
    }

    loadStations();
    return () => {
      ignore = true;
    };
  }, [toStation]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    abortControllerRef.current?.abort();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await searchRoutesStream(
        {
          from_station: fromStation,
          to_station: toStation,
          date,
          max_transfers: 1,
          min_transfer_minutes: minTransferMinutes,
        },
        (_, partialResponse) => setResult(partialResponse),
        abortController.signal,
      );
      setResult(response);
      setProviderStatus(await getProviderStatus());
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(formatSearchError(caught));
      }
    } finally {
      if (abortControllerRef.current === abortController) {
        abortControllerRef.current = null;
      }
      setIsLoading(false);
    }
  }

  function handleAbortSearch() {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsLoading(false);
  }

  return (
    <main className="min-h-screen bg-zinc-50 text-zinc-950">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 md:px-6">
        <header className="flex flex-col gap-2 border-b border-zinc-200 pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <TrainFront className="h-4 w-4" />
              HardSeat Hero
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-normal md:text-3xl">
              低价中转方案查询
            </h1>
          </div>
          <div className="text-sm text-zinc-500">
            数据源：{providerStatus?.provider ?? result?.source ?? "mock"}
          </div>
        </header>

        <section className="rounded-lg border border-zinc-200 bg-white p-3 text-sm text-zinc-600 shadow-sm">
          <div>
            中转候选算法：
            {providerStatus?.transfer_candidate_enabled
              ? "已启用真实数据源候选站生成"
              : "未启用真实数据源候选站生成（当前为 Mock 或状态未知）"}
          </div>
          <div>
            OD 请求预算：{providerStatus?.max_remote_queries ?? "-"}
            ；并发上限：
            {providerStatus?.max_concurrent_remote_queries ?? "-"}
            ；上次实际请求：
            {providerStatus?.last_remote_query_count ?? "-"}
          </div>
        </section>

        <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm">
          <form
            className="grid gap-4 md:grid-cols-[1fr_1fr_180px_170px_auto] md:items-end"
            onSubmit={handleSubmit}
          >
            <Field label="出发地">
              <Input
                list="from-station-options"
                value={fromStation}
                onChange={(event) => setFromStation(event.target.value)}
              />
            </Field>
            <Field label="目的地">
              <Input
                list="to-station-options"
                value={toStation}
                onChange={(event) => setToStation(event.target.value)}
              />
            </Field>
            <datalist id="from-station-options">
              {fromStationOptions.map((station) => (
                <option key={station} value={station} />
              ))}
            </datalist>
            <datalist id="to-station-options">
              {toStationOptions.map((station) => (
                <option key={station} value={station} />
              ))}
            </datalist>
            <Field label="日期">
              <Input
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </Field>
            <Field label="最短换乘">
              <Input
                type="number"
                min={0}
                max={360}
                value={minTransferMinutes}
                onChange={(event) =>
                  setMinTransferMinutes(Number(event.target.value))
                }
              />
            </Field>
            <Button disabled={isLoading} type="submit">
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              查询
            </Button>
            {isLoading ? (
              <Button type="button" onClick={handleAbortSearch}>
                <Square className="h-4 w-4" />
                中断
              </Button>
            ) : null}
          </form>
        </section>

        {error ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        <section className="grid gap-3">
          {result && result.plans.length === 0 ? (
            <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-sm text-zinc-500">
              暂无可用方案
            </div>
          ) : null}

          {result?.plans.map((plan, index) => (
            <article
              key={`${plan.total_price}-${index}`}
              className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm"
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="text-xl font-semibold">
                    ¥{Number(plan.total_price).toFixed(1)}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-3 text-sm text-zinc-500">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-4 w-4" />
                      {formatDuration(plan.total_duration_minutes)}
                    </span>
                    <span>
                      {plan.transfer_stations.length
                        ? `经 ${plan.transfer_stations.join("、")}`
                        : "直达"}
                    </span>
                    <span>换乘等待 {plan.transfer_minutes} 分钟</span>
                  </div>
                </div>
                <div className="text-sm text-zinc-500">
                  更新：{formatDateTime(result.updated_at)}
                </div>
              </div>

              <div className="mt-4 grid gap-2">
                {plan.segments.map((segment) => (
                  <div
                    key={`${segment.train_no}-${segment.depart_at}`}
                    className="grid gap-3 rounded-md bg-zinc-50 p-3 md:grid-cols-[120px_1fr_auto] md:items-center"
                  >
                    <div className="font-medium">{segment.train_no}</div>
                    <div className="flex items-center gap-2 text-sm">
                      <span>{segment.from_station}</span>
                      <span className="text-zinc-400">
                        {formatTime(segment.depart_at)}
                      </span>
                      <ArrowRight className="h-4 w-4 text-zinc-400" />
                      <span>{segment.to_station}</span>
                      <span className="text-zinc-400">
                        {formatTime(segment.arrive_at)}
                      </span>
                    </div>
                    <div className="text-sm text-zinc-600">
                      {segment.prices[0]?.seat_type} ¥
                      {Number(segment.prices[0]?.price ?? 0).toFixed(1)}
                    </div>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm font-medium text-zinc-700">
      {label}
      {children}
    </label>
  );
}

function formatDuration(minutes: number) {
  return `${Math.floor(minutes / 60)}小时${minutes % 60}分钟`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatSearchError(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.category === "data_source_unavailable") {
      return `数据源不可用：${caught.message}`;
    }
    if (caught.category === "bad_request") {
      return `查询参数错误：${caught.message}`;
    }
    return caught.message;
  }
  if (caught instanceof TypeError) {
    return "后端服务不可用，请确认 API 服务已启动。";
  }
  return caught instanceof Error ? caught.message : "查询失败";
}

function isAbortError(caught: unknown) {
  return caught instanceof DOMException && caught.name === "AbortError";
}
