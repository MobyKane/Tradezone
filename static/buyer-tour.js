(() => {
    const root = document.getElementById('buyer-tour');
    if (!root) return;

    const card = root.querySelector('.buyer-tour__card');
    const spotlight = root.querySelector('.buyer-tour__spotlight');
    const title = document.getElementById('buyer-tour-title');
    const copy = document.getElementById('buyer-tour-copy');
    const progress = document.getElementById('buyer-tour-progress');
    const announcement = document.getElementById('buyer-tour-announcement');
    const nextButton = root.querySelector('[data-tour-next]');
    const backButton = root.querySelector('[data-tour-back]');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const menuButton = document.querySelector('[data-tour-menu-button]');
    const sidebar = document.getElementById('sidebar');
    const steps = [
        {
            title: 'Welcome to TradeZone',
            copy: 'Shop products from independent vendors in one marketplace.',
            target: null,
            next: 'Start tour',
        },
        {
            title: 'Find what you need',
            copy: 'Search by product, brand, or vendor. You can narrow your browsing with the category links below.',
            target: '#tour-search',
        },
        {
            title: 'Your main navigation',
            copy: 'Home returns to the catalog, Cart holds your picks, Profile shows your orders, and Become a Seller starts store setup. Sellers see Sell here instead.',
            target: '#sidebar-links',
            mobileTarget: '[data-tour-menu-button]',
            mobileCopy: 'The menu is open. Home returns to the catalog, Cart holds your picks, Profile shows your orders, and Become a Seller starts store setup. Sellers see Sell here instead.',
            openMenu: true,
        },
        {
            title: 'Browse categories',
            copy: 'Choose Fashion, then Men, Women, or Unisex. Building Materials contains Spanish Tiles, Doors, and Other Building Materials.',
            target: '#tour-categories',
        },
        {
            title: 'Product details at a glance',
            copy: 'A listing shows the product image, price, and seller. Open a product page for full details, then use Add to Cart when you are ready.',
            target: '#tour-product-card, #tour-detail-info',
            emptyCopy: 'There are no listings yet. When products are available, each product page will show its images, price, seller, and Add to Cart button.',
        },
        {
            title: 'Review your cart',
            copy: 'Your cart shows each item and seller. Change a quantity and choose Update, or remove an item before checkout.',
            target: '#nav-cart',
        },
        {
            title: 'Checkout and payment',
            copy: 'Checkout uses Paystack for card, bank transfer, or USSD. Vendor earnings remain in protected escrow until delivery. Track progress and report an order issue from Profile and Orders; product pages also let you report a listing. This tour never begins a payment.',
            target: '#tour-checkout',
            fallback: '#nav-cart',
        },
        {
            title: "You're ready to shop",
            copy: 'Browse the categories or search the catalog whenever you are ready.',
            target: null,
            next: 'Browse categories',
            finish: true,
        },
    ];

    let index = 0;
    let active = false;
    let transitioning = false;
    let previousFocus = null;
    let menuOpenedByTour = false;
    let activeTarget = null;

    document.getElementById('buyer-tour-prompt')?.removeAttribute('hidden');

    function getTarget(step) {
        if (!step.target) return null;
        const selector = step.mobileTarget && window.matchMedia('(max-width: 767px)').matches ? step.mobileTarget : step.target;
        let target = document.querySelector(selector);
        if ((!target || !isVisible(target)) && step.fallback) target = document.querySelector(step.fallback);
        if (!target || !isVisible(target)) return null;
        return target;
    }

    function isVisible(element) {
        if (!element || !element.getClientRects().length) return false;
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    async function revealNavigation() {
        if (!menuButton || !sidebar) return;
        if (window.matchMedia('(max-width: 767px)').matches) {
            if (sidebar.classList.contains('-translate-x-full')) {
                menuButton.click();
                menuOpenedByTour = true;
            }
        } else {
            const links = document.getElementById('sidebar-links');
            if (links && window.getComputedStyle(links).display === 'none') {
                document.getElementById('sidebar-menu-toggle')?.click();
                menuOpenedByTour = true;
            }
        }
    }

    async function renderStep(requestedIndex) {
        if (transitioning) return;
        transitioning = true;
        nextButton.disabled = true;
        backButton.disabled = true;
        index = requestedIndex;
        card.classList.remove('is-changing');
        void card.offsetWidth;
        card.classList.add('is-changing');
        while (index >= 0 && index < steps.length) {
            const step = steps[index];
            if (step.openMenu) await revealNavigation();
            const target = getTarget(step);
            const showEmptyExplanation = step.emptyCopy && root.dataset.emptyCatalog === 'true';
            if (!step.target || target || showEmptyExplanation) {
                activeTarget = target;
                document.querySelectorAll('.buyer-tour-target').forEach(element => element.classList.remove('buyer-tour-target'));
                target?.classList.add('buyer-tour-target');
                if (target && !reducedMotion.matches) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
                } else if (target) {
                    target.scrollIntoView({ behavior: 'auto', block: 'center', inline: 'nearest' });
                }
                title.textContent = step.title;
                copy.textContent = index === 2 && step.mobileCopy && window.matchMedia('(max-width: 767px)').matches
                    ? step.mobileCopy
                    : target || !step.target ? step.copy : (showEmptyExplanation ? step.emptyCopy : step.copy);
                progress.textContent = `Step ${index + 1} of ${steps.length}`;
                announcement.textContent = `${progress.textContent}. ${step.title}. ${copy.textContent}`;
                nextButton.textContent = step.next || (step.finish ? 'Browse categories' : 'Next');
                backButton.hidden = index === 0;
                positionSpotlight(target);
                positionCard(target);
                card.querySelector('.buyer-tour__illustration').hidden = Boolean(target);
                transitioning = false;
                nextButton.disabled = false;
                backButton.disabled = index === 0;
                requestAnimationFrame(() => card.focus({ preventScroll: true }));
                return;
            }
            index += 1;
        }
        transitioning = false;
        finishTour(false);
    }

    function positionSpotlight(target) {
        if (!target) {
            spotlight.hidden = true;
            return;
        }
        spotlight.hidden = false;
        const rect = target.getBoundingClientRect();
        const padding = 8;
        spotlight.style.left = `${Math.max(4, rect.left - padding)}px`;
        spotlight.style.top = `${Math.max(4, rect.top - padding)}px`;
        spotlight.style.width = `${Math.min(window.innerWidth - 8, rect.width + padding * 2)}px`;
        spotlight.style.height = `${Math.min(window.innerHeight - 8, rect.height + padding * 2)}px`;
        spotlight.style.borderRadius = getComputedStyle(target).borderRadius || '8px';
    }

    function positionCard(target) {
        const margin = 16;
        const cardWidth = Math.min(380, window.innerWidth - margin * 2);
        card.style.width = `${cardWidth}px`;
        const cardHeight = card.getBoundingClientRect().height || 290;
        let top = (window.innerHeight - cardHeight) / 2;
        let left = (window.innerWidth - cardWidth) / 2;
        if (target) {
            const rect = target.getBoundingClientRect();
            left = Math.min(Math.max(margin, rect.left + rect.width / 2 - cardWidth / 2), window.innerWidth - cardWidth - margin);
            if (rect.bottom + cardHeight + 20 <= window.innerHeight) {
                top = rect.bottom + 16;
                card.dataset.placement = 'below';
            } else if (rect.top - cardHeight - 16 >= margin) {
                top = rect.top - cardHeight - 16;
                card.dataset.placement = 'above';
            } else {
                card.dataset.placement = 'center';
            }
            const arrowLeft = Math.min(Math.max(rect.left + rect.width / 2 - left, 18), cardWidth - 18);
            card.style.setProperty('--tour-arrow-left', `${arrowLeft}px`);
            card.classList.add('has-target');
        } else {
            card.classList.remove('has-target');
            card.dataset.placement = 'center';
        }
        card.style.left = `${Math.max(margin, left)}px`;
        card.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - cardHeight - margin))}px`;
    }

    function setGuestCookie() {
        document.cookie = 'tradezone_buyer_tour=done; max-age=31536000; path=/; samesite=lax';
    }

    async function persistCompletion() {
        setGuestCookie();
        const csrf = root.querySelector('input[name=csrfmiddlewaretoken]')?.value || '';
        try {
            await fetch(root.dataset.completeUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
            });
        } catch (error) {
            // The guest cookie still prevents the prompt from returning if offline.
        }
    }

    function closeMenuIfNeeded() {
        if (!menuOpenedByTour) return;
        if (window.matchMedia('(max-width: 767px)').matches && !sidebar.classList.contains('-translate-x-full')) {
            menuButton?.click();
        } else if (window.matchMedia('(min-width: 768px)').matches && sidebar.classList.contains('is-expanded')) {
            document.getElementById('sidebar-menu-toggle')?.click();
        }
        menuOpenedByTour = false;
    }

    async function finishTour(persist = true) {
        active = false;
        root.hidden = true;
        root.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('buyer-tour-open');
        document.querySelectorAll('.buyer-tour-target').forEach(element => element.classList.remove('buyer-tour-target'));
        closeMenuIfNeeded();
        if (persist) await persistCompletion();
        if (previousFocus && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
    }

    function startTour() {
        if (active) return;
        previousFocus = document.activeElement;
        active = true;
        index = 0;
        root.hidden = false;
        root.setAttribute('aria-hidden', 'false');
        document.body.classList.add('buyer-tour-open');
        renderStep(index);
    }

    nextButton.addEventListener('click', () => {
        if (steps[index].finish) {
            const categories = document.querySelector('#tour-categories');
            finishTour(true);
            categories?.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
            return;
        }
        renderStep(index + 1);
    });
    backButton.addEventListener('click', () => renderStep(Math.max(0, index - 1)));
    root.querySelectorAll('[data-tour-skip], [data-tour-close]').forEach(button => button.addEventListener('click', () => finishTour(true)));
    document.querySelectorAll('[data-tour-start]').forEach(button => button.addEventListener('click', startTour));
    document.querySelectorAll('[data-tour-dismiss]').forEach(button => button.addEventListener('click', async () => {
        document.getElementById('buyer-tour-prompt')?.remove();
        await persistCompletion();
    }));

    document.addEventListener('keydown', event => {
        if (!active) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            finishTour(true);
        } else if (event.key === 'ArrowRight' || (event.key === 'Enter' && event.target === card)) {
            if (event.target.closest('.buyer-tour__card')) {
                event.preventDefault();
                nextButton.click();
            }
        } else if (event.key === 'ArrowLeft' && event.target.closest('.buyer-tour__card')) {
            event.preventDefault();
            backButton.click();
        } else if (event.key === 'Tab') {
            const focusable = [...card.querySelectorAll('button:not([hidden])')];
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (!card.contains(document.activeElement)) {
                event.preventDefault();
                first.focus();
            } else if (event.shiftKey && (document.activeElement === first || document.activeElement === card)) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
    window.addEventListener('resize', () => {
        if (active) {
            positionSpotlight(activeTarget);
            positionCard(activeTarget);
        }
    });
    window.addEventListener('scroll', () => {
        if (active) {
            positionSpotlight(activeTarget);
            positionCard(activeTarget);
        }
    }, { passive: true });

    if (root.dataset.autostart === 'true') startTour();
})();
