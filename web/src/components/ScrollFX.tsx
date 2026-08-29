import { useEffect, useRef } from "react";
import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/* Page-wide scroll feel: smooth scrolling, a petal field that answers to the scroll, and
 * 3D reveals on anything marked up for them.
 *
 * SMOOTH SCROLL. Lenis interpolates toward the native scroll position instead of jumping to
 * it, which is what gives Mercury its weight. It has to be wired into GSAP's ticker rather
 * than its own rAF loop -- running two independent loops leaves ScrollTrigger reading a
 * scroll position one frame behind Lenis, and every pinned element jitters.
 *
 * PETALS THAT ANSWER THE SCROLL. Drawn on a canvas because forty DOM nodes each with their
 * own transform is forty style recalculations a frame. Scroll VELOCITY, not position, feeds
 * their drift and spin, so flicking the wheel scatters them and stopping lets them settle.
 * That is the whole trick: the blossom reacts to you rather than looping past you.
 *
 * 3D REVEALS. Anything with data-reveal rises out of Z with a slight X-rotation as it
 * enters. The parent needs a perspective for that to mean anything -- without one a
 * translateZ is silently ignored, which is why most "3D" web animation looks flat.
 *
 * EVERYTHING HERE IS DECORATION AND FAILS SOFT. Reveals start visible in CSS and are only
 * hidden once JS confirms it can animate them; if the script never runs, or the tab is
 * backgrounded where requestAnimationFrame is suspended, the page reads as a normal
 * document instead of a blank one.
 */

const PETAL_COUNT = 46;
const PETAL_TINTS = ["#ffd6e2", "#ffc2d4", "#f9aec5", "#f7bfd0"];

type Petal = {
  x: number; y: number; z: number;      // z drives size and parallax speed
  r: number; spin: number; rot: number;
  sway: number; phase: number; tint: string;
};

export function usePetalField(canvasRef: React.RefObject<HTMLCanvasElement>) {
  const velRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let w = 0, h = 0, dpr = 1;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    // Deterministic seeding: a fixed sequence means the field looks the same on every load
    // rather than occasionally clumping into an obvious stripe.
    let seed = 20260822;
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);

    const petals: Petal[] = Array.from({ length: PETAL_COUNT }, () => ({
      x: rnd() * w,
      y: rnd() * h,
      z: 0.35 + rnd() * 0.9,
      r: 4 + rnd() * 7,
      spin: (rnd() - 0.5) * 0.02,
      rot: rnd() * Math.PI * 2,
      sway: 0.35 + rnd() * 0.9,
      phase: rnd() * Math.PI * 2,
      tint: PETAL_TINTS[Math.floor(rnd() * PETAL_TINTS.length)],
    }));

    const draw = (t: number) => {
      ctx.clearRect(0, 0, w, h);
      // Velocity decays toward rest, so the scatter eases out instead of stopping dead.
      velRef.current *= 0.92;
      const v = velRef.current;

      for (const p of petals) {
        // Deeper petals fall slower and are pushed less by the scroll: that difference is
        // the parallax, and it is what makes a flat canvas read as having depth.
        p.y += (0.28 + p.z * 0.5) + v * p.z * 0.55;
        p.x += Math.sin(t * 0.0004 + p.phase) * p.sway * 0.5 - v * p.z * 0.12;
        p.rot += p.spin + v * 0.0016;

        if (p.y > h + 20) { p.y = -20; p.x = rnd() * w; }
        if (p.y < -40) { p.y = h + 20; }
        if (p.x < -30) p.x = w + 25;
        if (p.x > w + 30) p.x = -25;

        const s = p.r * p.z;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.globalAlpha = 0.28 + p.z * 0.42;
        ctx.fillStyle = p.tint;
        // A petal, not a circle: two arcs meeting at a point.
        ctx.beginPath();
        ctx.moveTo(0, -s);
        ctx.quadraticCurveTo(s * 0.92, -s * 0.28, 0, s);
        ctx.quadraticCurveTo(-s * 0.92, -s * 0.28, 0, -s);
        ctx.fill();
        ctx.restore();
      }
    };

    const tick = (time: number) => draw(time * 1000);
    gsap.ticker.add(tick);

    return () => {
      gsap.ticker.remove(tick);
      window.removeEventListener("resize", resize);
    };
  }, [canvasRef]);

  return velRef;
}

export default function ScrollFX() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const vel = usePetalField(canvasRef);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      document.documentElement.classList.add("fx-ready");
      return;
    }

    const lenis = new Lenis({
      duration: 1.05,
      // Slightly overshooting ease: the page settles rather than arriving abruptly.
      easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      wheelMultiplier: 0.95,
      touchMultiplier: 1.6,
      // Never smooth touch scrolling. Fighting a phone's native scroll physics feels
      // broken in a way no amount of easing fixes.
      syncTouch: false,
    });

    // One loop, not two. Lenis must be driven by GSAP's ticker and ScrollTrigger updated
    // from Lenis, or the two read scroll positions a frame apart and pinned elements jitter.
    lenis.on("scroll", (e: { velocity: number }) => {
      ScrollTrigger.update();
      vel.current = gsap.utils.clamp(-26, 26, e.velocity ?? 0);
    });
    const raf = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    // Hiding the reveal targets is only safe if the animation frame is genuinely running.
    //
    // Adding this class puts twenty elements at opacity 0 and hands responsibility for
    // showing them to rAF-driven tweens. A backgrounded tab suspends rAF completely --
    // measured at zero frames per second -- so on its own this recreates exactly the
    // failure it is meant to avoid: a page that renders blank. The watchdog below removes
    // the class again if no frame arrives, and the visibility listener restores the effect
    // when the tab is brought forward.
    document.documentElement.classList.add("fx-ready");

    let frames = 0;
    const countFrame = () => { frames++; };
    gsap.ticker.add(countFrame);

    const watchdog = window.setTimeout(() => {
      if (frames < 2) {
        // Nothing is animating. Reveal everything rather than leave the page empty.
        //
        // Dropping the class is NOT enough on its own: gsap.fromTo renders its start state
        // immediately, writing inline opacity:0 onto every target the moment the tween is
        // created. An inline style beats a stylesheet rule, so removing the class left
        // eighteen of twenty elements still invisible. The tweens have to be killed and
        // their inline properties cleared.
        document.documentElement.classList.remove("fx-ready");
        const targets = gsap.utils.toArray<HTMLElement>("[data-reveal]");
        gsap.killTweensOf(targets);
        gsap.set(targets, { clearProps: "all" });
      }
      gsap.ticker.remove(countFrame);
    }, 1200);

    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      document.documentElement.classList.add("fx-ready");
      ScrollTrigger.refresh();
    };
    document.addEventListener("visibilitychange", onVisible);

    const ctx = gsap.context(() => {
      gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((el) => {
        const kind = el.dataset.reveal || "rise";
        const from: gsap.TweenVars =
          kind === "tilt"
            ? { opacity: 0, y: 70, z: -260, rotateX: 14, transformOrigin: "50% 100%" }
            : kind === "swing"
            ? { opacity: 0, y: 40, z: -180, rotateY: -12, transformOrigin: "0% 50%" }
            : { opacity: 0, y: 54, z: -140 };

        gsap.fromTo(el, from, {
          opacity: 1, y: 0, z: 0, rotateX: 0, rotateY: 0,
          duration: 1.05,
          ease: "power3.out",
          scrollTrigger: {
            trigger: el,
            start: "top 88%",
            once: true,
          },
        });
      });

      // Depth: marked layers drift at their own rate against the page.
      gsap.utils.toArray<HTMLElement>("[data-parallax]").forEach((el) => {
        const depth = parseFloat(el.dataset.parallax || "0.12");
        gsap.to(el, {
          yPercent: -depth * 100,
          ease: "none",
          scrollTrigger: {
            trigger: el.closest("section") || el,
            start: "top bottom",
            end: "bottom top",
            scrub: 0.8,
          },
        });
      });
    });

    ScrollTrigger.refresh();

    return () => {
      window.clearTimeout(watchdog);
      gsap.ticker.remove(countFrame);
      document.removeEventListener("visibilitychange", onVisible);
      ctx.revert();
      gsap.ticker.remove(raf);
      lenis.destroy();
      document.documentElement.classList.remove("fx-ready");
    };
  }, [vel]);

  return <canvas ref={canvasRef} className="fx-petals" aria-hidden="true" />;
}
